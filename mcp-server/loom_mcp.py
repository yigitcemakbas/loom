"""Loom's evidence engine, as an MCP server.

Exposes the `/v1/evidence` surface to any MCP client: Claude Desktop, Claude
Code, Cursor, or ChatGPT through its Apps SDK, which is built on the same
protocol. One implementation reaches all of them, which is why this is an MCP
server rather than a ChatGPT-specific integration.

**A thin HTTP client over the evidence API, holding no database connection and
importing nothing from the engine.** That is the architecture, not an
implementation shortcut. It means this process can run anywhere, that it cannot
outlive or corrupt Loom's state, and most usefully that it inherits Loom's
read-only guarantee from its own credential: a Loom API key reaches read routes
and nothing else, so this server is incapable of adding a ticker, dismissing a
finding or spending the instance's model quota however it is driven.

**It serves evidence and no verdict, and that omission is the point.** In the
reader benchmark, agents given Loom's evidence plus its directional verdict
scored 10.10 points below agents given the same evidence alone (p=0.005): they
used 29% fewer of the underlying findings, agreed with Loom twice as often, and
grew more confident while getting less accurate. The best cell in the grid was
evidence without a verdict. The API has no endpoint that returns a stance, so
there is nothing here to withhold by choice.

Run:  LOOM_API_KEY=loom_sk_... python loom_mcp.py
"""

from __future__ import annotations

import functools
import os
from typing import Any, Callable, Optional

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

# A deployed instance serves the API under /api on the same origin as the
# interface; a local compose stack publishes the backend directly on 8000.
API_URL = os.environ.get("LOOM_API_URL", "http://localhost:8000").rstrip("/")
API_KEY = os.environ.get("LOOM_API_KEY", "").strip()

TIMEOUT = httpx.Timeout(60.0, connect=10.0)

# Read by the client before any tool is called, so it is the one place to say
# what this data is and what it is not. Written for a model rather than a
# person: the failure it exists to prevent is an agent treating a documentary
# direction as a price forecast, or weighting a tone judgement like a filing
# comparison.
INSTRUCTIONS = """\
Loom reads SEC filings, earnings call transcripts, insider transactions and \
news, and reports what the documents say with the passage and source for every \
claim.

Start with `loom_capabilities`. It describes the surface, how each kind of \
finding was established, and what each source is good for, without needing \
this text.

Four things to know before using the findings:

1. `direction` describes what a document did, not what a share price will do. A \
risk appearing is a negative *document*. Loom contains no pricing model and \
makes no forecast; treating direction as a prediction misreads it.

2. `how_established` is the field to weight by. A deterministic comparison of \
two filings is checkable against the source text; a judgement about management \
tone is not. They are not equally strong evidence and the field says which is \
which.

3. `as_of` makes every query point-in-time. Pass a date and you see only what \
was on file then, with restatements resolved to the figure filed at the time. \
This is what makes Loom usable for checking reasoning about a past date.

4. Sources are described, not ranked. A filing is authoritative and complete; a \
news item is faster and is often the only source for something not yet filed. \
Check `source.loom_holds`: Loom stores filings and transcripts in full and news \
as headline and summary, so expect a publisher's page to say more than a news \
quote rather than to contradict it.

Loom does not serve a directional verdict. That is deliberate and measured: \
agents given the evidence plus Loom's stance performed worse than agents given \
the evidence alone. Form your own view from the findings.\
"""

server = MCPServer(
    name="loom",
    title="Loom equity research",
    instructions=INSTRUCTIONS,
    version="1.0.0",
    website_url="https://github.com/yigitcemakbas/loom",
)

# Every tool here reads. Declared rather than merely true, so a client can tell
# a user this server cannot change anything, and so an agent planning actions
# knows none of these need confirmation.
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False,
                            idempotent_hint=True, open_world_hint=False)


class LoomError(RuntimeError):
    """A failure worth returning to the model in words it can act on."""


def reported(func: Callable) -> Callable:
    """Return failures as content instead of raising them.

    The SDK converts an exception escaping a tool into `UnexpectedToolError`
    with the message "Error executing tool <name>", discarding whatever the
    exception said. An agent reading that learns only that something went wrong,
    not that the API key is missing or that Loom is not running, so every
    carefully worded diagnostic in this file was being thrown away before it
    reached the one reader who could act on it.

    Returning the message as the tool's result keeps it: a model sees the
    explanation and the remedy, and an `error` key is unambiguous enough that it
    will not be mistaken for evidence.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except LoomError as exc:
            return {"error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"Unexpected failure talking to Loom: {type(exc).__name__}: {exc}"}

    return wrapper


def _get(path: str, params: Optional[dict] = None) -> Any:
    """One GET against the evidence API.

    Failures are translated into sentences rather than status codes, because the
    caller is a language model: "the key was rejected" leads somewhere and a
    bare 401 does not.
    """
    if not API_KEY:
        raise LoomError(
            "No Loom API key is configured. Set LOOM_API_KEY in this server's "
            "environment. Issue a key by signing in to Loom and calling "
            "POST /auth/api-keys."
        )

    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            response = client.get(
                f"{API_URL}/v1/evidence{path}",
                params={k: v for k, v in (params or {}).items() if v is not None},
                headers={"Authorization": f"Bearer {API_KEY}"},
            )
    except httpx.RequestError as exc:
        raise LoomError(
            f"Could not reach Loom at {API_URL}. It may not be running, or "
            f"LOOM_API_URL may be wrong. ({type(exc).__name__})"
        ) from exc

    if response.status_code == 401:
        raise LoomError(
            "Loom rejected the API key. It may have been revoked; issue a new "
            "one with POST /auth/api-keys."
        )
    if response.status_code == 404:
        raise LoomError(
            f"Loom has nothing for that path ({path}). For a ticker, this "
            f"usually means the company is not tracked: `loom_coverage` lists "
            f"the companies Loom has read."
        )
    if response.status_code == 400:
        detail = ""
        try:
            detail = response.json().get("detail", "")
        except Exception:
            detail = response.text[:200]
        raise LoomError(f"Loom rejected the request: {detail}")
    if response.status_code >= 500:
        raise LoomError(f"Loom returned an error ({response.status_code}).")

    response.raise_for_status()
    return response.json()


@server.tool(
    name="loom_capabilities",
    description=(
        "What Loom's evidence engine serves, what it deliberately withholds, how "
        "each kind of finding was established, and what each source is good for. "
        "Call this first: it describes the whole surface and the limits of the data."
    ),
    annotations=READ_ONLY,
)
@reported
def loom_capabilities() -> dict:
    return _get("/capabilities")


@server.tool(
    name="loom_coverage",
    description=(
        "Which companies Loom has actually read, ordered by how deeply. Use this "
        "before asking about a ticker: a company absent from this list has no "
        "document evidence, which is different from having unremarkable evidence."
    ),
    annotations=READ_ONLY,
)
@reported
def loom_coverage(as_of: Optional[str] = None, limit: int = 50) -> dict:
    """as_of: ISO date (YYYY-MM-DD) to see coverage as it stood then."""
    return _get("/coverage", {"as_of": as_of, "limit": limit})


@server.tool(
    name="loom_evidence",
    description=(
        "Everything Loom has read about one company: findings with verbatim "
        "passages and sources, contradictions between its own sources, observed "
        "and peer-expected disclosure counts, factor percentiles, dependency "
        "edges, and how much has been read. No directional verdict. Pass as_of "
        "to see only what was knowable on that date."
    ),
    annotations=READ_ONLY,
)
@reported
def loom_evidence(ticker: str, as_of: Optional[str] = None,
                  findings_limit: int = 40) -> dict:
    """ticker: US exchange symbol. as_of: ISO date for a point-in-time view."""
    return _get(f"/{ticker.upper()}", {"as_of": as_of, "findings_limit": findings_limit})


@server.tool(
    name="loom_findings",
    description=(
        "Findings for one company, paginated and filterable by kind, each with a "
        "verbatim passage, its source document, and how it was established. "
        "Kinds: new_risk_factor, resolved_risk_factor, qoq_anomaly, "
        "guidance_change, insider_activity, short_interest_spike, "
        "emerging_pattern, notable_quote, sentiment_shift."
    ),
    annotations=READ_ONLY,
)
@reported
def loom_findings(ticker: str, kind: Optional[str] = None,
                  as_of: Optional[str] = None, limit: int = 25,
                  offset: int = 0) -> dict:
    return _get(
        f"/{ticker.upper()}/findings",
        {"kind": kind, "as_of": as_of, "limit": limit, "offset": offset},
    )


@server.tool(
    name="loom_filing_changes",
    description=(
        "What the newest filing added and what it withdrew, against its "
        "predecessor. Both directions: a withdrawn risk is a company dropping a "
        "disclosure it previously felt obliged to make. Paragraphs here are "
        "candidates established by text comparison, not conclusions."
    ),
    annotations=READ_ONLY,
)
@reported
def loom_filing_changes(ticker: str, section: str = "1A",
                        as_of: Optional[str] = None) -> dict:
    """section: '1A' for a 10-K's risk factors, '2' for a 10-Q's discussion."""
    return _get(f"/{ticker.upper()}/changes", {"section": section, "as_of": as_of})


if __name__ == "__main__":
    # stdio by default: that is what Claude Desktop, Claude Code and Cursor
    # launch, and it needs no network exposure. Set LOOM_MCP_TRANSPORT to
    # streamable-http for a remote client such as ChatGPT, which also needs the
    # server reachable over HTTPS.
    server.run(transport=os.environ.get("LOOM_MCP_TRANSPORT", "stdio"))
