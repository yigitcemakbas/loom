"""The evidence API, and the one guarantee it exists to make.

The reader benchmark found that handing an agent Loom's evidence *and* its verdict
scored 10.10 points below handing it the same evidence alone (p=0.005), with agents
using 29% fewer of the underlying findings and growing more confident while getting
less accurate. This API is that finding turned into a product surface.

A guarantee enforced by remembering to strip a field is not a guarantee, so most of
these tests are about structure rather than behaviour: the module cannot reach a
stance because it never imports the thing that computes one, and no schema in it
has a field that could carry one. That is checkable without a database, which is
also why it is checked here — the rest of this suite runs on SQLite and this app's
real schema needs Postgres column types.
"""

import ast
import pathlib
from datetime import date, datetime, timezone

from pydantic import BaseModel

from app.api.routes import evidence
from app.models.signal import SignalType

_SOURCE = pathlib.Path(evidence.__file__).read_text()


# ---- the guarantee ----------------------------------------------------------


def test_the_module_never_imports_anything_that_computes_a_stance():
    """The structural claim, checked as a fact about the imports.

    `engine/brief.py` is the only thing in the codebase that produces a stance,
    and a `Brief` is the only object that carries one. This module cannot leak a
    verdict because it has no way to obtain one — which is a stronger property
    than filtering a field out, and unlike a filter it cannot be undone by a
    later endpoint forgetting about it.
    """
    tree = ast.parse(_SOURCE)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            for alias in node.names:
                imported.add(f"{node.module}.{alias.name}")
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)

    assert not any("brief" in name.lower() for name in imported), (
        f"The evidence API must not import the brief engine. Found: "
        f"{[n for n in imported if 'brief' in n.lower()]}"
    )
    assert "build_brief" not in _SOURCE


def test_no_response_schema_has_a_field_that_could_carry_a_verdict():
    """Every Pydantic model in the module, checked field by field.

    Named fields rather than a blanket rule, because the words matter: `direction`
    is allowed and is the point — it describes what a document did. `stance`,
    `verdict` and `recommendation` are Loom's own conclusion, which is what is
    withheld.
    """
    forbidden = {
        "stance", "verdict_label", "recommendation", "rating", "conviction",
        "target_price", "score", "signal_strength",
    }
    models = [
        value for value in vars(evidence).values()
        if isinstance(value, type) and issubclass(value, BaseModel)
        and value is not BaseModel
    ]
    assert models, "No schemas found; this test would pass vacuously."

    for model in models:
        overlap = forbidden & set(model.model_fields)
        assert not overlap, f"{model.__name__} exposes {overlap}"


def test_the_packet_verdict_field_can_only_ever_be_null():
    """It is typed `None`, not `Optional[str]`.

    Present as a field so an agent reading the schema learns the omission is
    deliberate and is told why, rather than inferring Loom has no view. Typed so
    that populating it would be a validation error rather than a regression
    nobody notices.
    """
    field = evidence.EvidencePacket.model_fields["verdict"]

    assert field.annotation is type(None)
    assert field.default is None
    # The reason travels with the schema, so an agent that reads only the
    # generated OpenAPI still learns why.
    assert "benchmark" in (field.description or "").lower()


# ---- what the findings carry ------------------------------------------------


def test_every_signal_type_says_how_it_was_established():
    """No finding may reach an agent labelled "unspecified".

    The benchmark's mechanism was agents weighting an unverifiable language
    judgement the same as a deterministic two-filing comparison. That distinction
    is the most useful thing this API can carry, so a new signal type must not be
    able to appear without one.
    """
    for signal_type in SignalType:
        assert signal_type.value in evidence.HOW_ESTABLISHED, (
            f"{signal_type.value} has no how_established entry"
        )


def test_the_least_verifiable_kind_says_so_plainly():
    """An agent should not have to infer this from a priority number."""
    assert "least verifiable" in evidence.HOW_ESTABLISHED["sentiment_shift"]
    assert "deterministic" in evidence.HOW_ESTABLISHED["new_risk_factor"]
    assert "no model" in evidence.HOW_ESTABLISHED["insider_activity"]


def test_a_quote_is_described_as_verbatim_rather_than_as_a_summary():
    """The quote is the thing an agent can check. Saying so is part of the
    contract, because a paraphrase that looks like a quote is worse than no
    quote at all."""
    assert "verbatim" in (evidence.Finding.model_fields["quote"].description or "").lower()


def test_direction_is_documented_as_documentary_and_not_as_a_forecast():
    """The semantic bug this whole engine was corrected for, now stated in the
    API's own schema so a caller cannot make the same mistake Loom did: a risk
    appearing is a negative *document*, not a prediction about the share price."""
    described = (evidence.Finding.model_fields["direction"].description or "").lower()

    assert "not a prediction" in described
    assert "share price" in described


# ---- point in time ----------------------------------------------------------


def test_as_of_resolves_to_the_end_of_that_day():
    """An agent asking about a date means the whole day, including a filing made
    that afternoon. Resolving to midnight would silently drop it."""
    cutoff = evidence._cutoff(date(2026, 3, 14))

    assert cutoff.year == 2026 and cutoff.month == 3 and cutoff.day == 14
    assert cutoff.hour == 23 and cutoff.minute == 59
    assert cutoff.tzinfo is timezone.utc


def test_no_as_of_means_now_rather_than_an_open_interval():
    """A missing cutoff must not become "no filter". Findings carry future-dated
    `occurred_at` in no legitimate case, but the query should not depend on that
    being true."""
    before = datetime.now(timezone.utc)
    cutoff = evidence._cutoff(None)

    assert cutoff >= before
    assert cutoff.tzinfo is timezone.utc


def test_every_company_endpoint_accepts_as_of():
    """Point-in-time is the property that makes this API usable for evaluating an
    agent's own past reasoning, so it belongs on every route rather than on the
    ones that happened to need it first."""
    for route in evidence.router.routes:
        if "{ticker}" not in route.path and not route.path.endswith("coverage"):
            continue
        names = {p.name for p in route.dependant.query_params}
        assert "as_of" in names, f"{route.path} does not accept as_of"


# ---- access -----------------------------------------------------------------


def test_every_endpoint_requires_a_caller_to_be_authenticated():
    """Checked for every route rather than reviewed once.

    An unauthenticated write endpoint already shipped in this codebase once and
    survived several reviews because nothing asserted the property. An API whose
    stated purpose is machine access is exactly where that recurs.
    """
    def resolves_a_user(dependant) -> bool:
        """Whether `current_user` appears anywhere in this route's chain."""
        for dependency in dependant.dependencies:
            name = getattr(dependency.call, "__name__", "")
            if name == "current_user" or resolves_a_user(dependency):
                return True
        return False

    assert evidence.router.routes, "No routes found; this test would pass vacuously."
    for route in evidence.router.routes:
        assert resolves_a_user(route.dependant), (
            f"{route.path} is reachable without authentication"
        )


# ---- honesty about coverage -------------------------------------------------


def test_a_thin_reading_is_disclosed_rather_than_presented_as_complete():
    """A packet that looks complete when Loom has read one filing is the most
    misleading thing this endpoint could return, so the caveat is a required
    field and not an optional note."""
    field = evidence.Coverage.model_fields["caveat"]

    assert field.is_required()


def test_disclosure_volume_serves_counts_and_never_the_residual():
    """Loom computes a genre-relative residual internally and deliberately does
    not serve it. The subtraction is one step for the caller and doing it here
    would be Loom forming the judgement this API exists to leave alone."""
    fields = set(evidence.DisclosureVolume.model_fields)

    assert {"observed_negative", "expected_negative"} <= fields
    assert not any("residual" in f or "excess" in f for f in fields)


def test_an_untrusted_peer_expectation_is_null_and_its_sample_is_shown():
    """Null rather than a number, and the document count alongside it, so a
    caller can tell "no peer norm" from "a peer norm of zero"."""
    fields = evidence.DisclosureVolume.model_fields

    assert fields["expected_negative"].default is None
    assert fields["peer_documents"].is_required()


# ---- the discovery endpoint -------------------------------------------------


def test_capabilities_states_the_omission_and_its_measured_reason():
    """Self-describing so an agent needs no documentation, and honest about the
    one thing deliberately missing — including the number, so the decision can be
    argued with rather than merely trusted."""
    described = evidence.capabilities.__doc__ or ""
    assert "withholds" in _SOURCE

    # The claim is quantified in the module rather than hand-waved.
    assert "10.10" in _SOURCE
    assert "p=0.005" in _SOURCE
    assert "withhold" in described.lower() or "withholds" in _SOURCE


def test_nothing_here_is_offered_as_a_price_forecast():
    assert "price_forecasts" in _SOURCE
    assert "Nothing here predicts a price" in _SOURCE


# ---- filtering ---------------------------------------------------------------


def test_the_kind_filter_narrows_the_query_and_not_the_finished_page():
    """Caught live: asking for five risk factors returned two.

    The filter ran over a page the database had already cut to the top five
    findings by priority, so a caller asking for fifty of one kind received
    however many of that kind happened to fall inside the top fifty overall —
    while the response still reported a successful page. `_findings` takes the
    kind so the predicate reaches the SQL.
    """
    import inspect

    assert "kind" in inspect.signature(evidence._findings).parameters
    body = inspect.getsource(evidence.findings)
    assert "if kind" not in body, "kind is being applied after the page was cut"


def test_an_unknown_kind_is_rejected_rather_than_silently_matching_nothing():
    """An empty list for a typo is indistinguishable from a company genuinely
    having none of that finding, and only one of those is safe to act on."""
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as raised:
        evidence._signal_type("new_risk_factors")

    assert raised.value.status_code == 400
    # The error names the valid kinds, so a caller can correct it without docs.
    assert "new_risk_factor" in raised.value.detail


def test_a_document_with_no_directional_findings_is_not_framed_as_a_deficit():
    """Both counts zero means the document was read and yielded nothing. The
    engine skips these rather than scoring them, because silence is neither good
    news nor bad; the schema carries that reasoning to the caller."""
    described = (evidence.DisclosureVolume.__doc__ or "").lower()

    assert "not a deficit" in described
    assert "silence is not good news" in described
