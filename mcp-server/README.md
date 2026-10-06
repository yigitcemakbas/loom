# Loom MCP server

Exposes Loom's evidence engine to any MCP client: Claude Desktop, Claude Code,
Cursor, and ChatGPT through its Apps SDK, which is built on the same protocol.

One implementation reaches all of them. ChatGPT's plugin system closed in April
2024 and was replaced by MCP-backed apps, so there is no ChatGPT-specific
integration to write.

## What it serves

| Tool | Returns |
|---|---|
| `loom_capabilities` | The surface, how each finding kind is established, what each source offers |
| `loom_coverage` | Companies Loom has read, by depth |
| `loom_evidence` | Full packet for one company |
| `loom_findings` | Paginated findings, filterable by kind |
| `loom_filing_changes` | Paragraphs added to and withdrawn from the latest filing |

Every tool reads. The server holds no database connection and imports nothing
from Loom's engine: it is an HTTP client over `/v1/evidence`, so it inherits
Loom's read-only guarantee from its own credential. A Loom API key reaches read
routes and nothing else, so this server cannot add a ticker, dismiss a finding,
or spend the instance's model quota however it is driven.

It serves no directional verdict, because the API has no endpoint that returns
one. In Loom's reader benchmark, agents given the evidence plus Loom's stance
scored 10.10 points below agents given the same evidence alone (p=0.005), used
29% fewer of the underlying findings, and grew more confident while getting less
accurate.

## Setup

Install:

```bash
cd mcp-server
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

Issue a key. Sign in to Loom, then:

```bash
curl -X POST $LOOM_API/auth/api-keys \
  -H "Authorization: Bearer $SESSION_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"mcp server"}'
```

The response carries the key once. Loom stores only a digest and cannot show it
again.

| Variable | Default | Purpose |
|---|---|---|
| `LOOM_API_KEY` | none | Required. The `loom_sk_…` key |
| `LOOM_API_URL` | `http://localhost:8000` | A deployed instance serves the API under `/api` |
| `LOOM_MCP_TRANSPORT` | `stdio` | `streamable-http` for a remote client |

### Claude Desktop, Claude Code, Cursor

These launch the server over stdio and need no network exposure. Add to the
client's MCP configuration:

```json
{
  "mcpServers": {
    "loom": {
      "command": "/absolute/path/to/loom/mcp-server/.venv/bin/python",
      "args": ["/absolute/path/to/loom/mcp-server/loom_mcp.py"],
      "env": {
        "LOOM_API_KEY": "loom_sk_...",
        "LOOM_API_URL": "http://localhost:8000"
      }
    }
  }
}
```

Absolute paths for both: these clients do not run from this directory and do not
inherit a shell `PATH`.

### ChatGPT

Needs the server reachable over HTTPS rather than launched locally:

```bash
LOOM_MCP_TRANSPORT=streamable-http LOOM_API_KEY=loom_sk_... .venv/bin/python loom_mcp.py
```

Publishing to ChatGPT's app directory additionally requires OAuth 2.1 with
dynamic client registration, which this server does not implement. A single
bearer key works in ChatGPT's developer mode, which is enough to use and test it
without a submission.

## Checking it works

```bash
LOOM_API_KEY=loom_sk_... .venv/bin/python -c "
import asyncio, json, loom_mcp
r = asyncio.run(loom_mcp.server.call_tool('loom_coverage', {'limit': 3}))
print(json.loads(r.content[0].text))
"
```

A failure returns an `error` key explaining what to do rather than raising: the
SDK replaces an escaping exception with a generic message, which would discard
the diagnostic before the model saw it.

## What a client reads first

The server ships instructions that tell an agent four things the data will not
tell it on its own:

- `direction` describes what a document did, not what a price will do
- `how_established` is the field to weight by; a two-filing comparison and a
  tone judgement are not equally strong evidence
- `as_of` makes every query point-in-time, with restatements resolved to the
  figure filed at the time
- sources are described rather than ranked, and `source.loom_holds` says whether
  Loom has the full document or only a headline and summary
