"""The evidence API: Loom's reading, served to an agent, without the verdict.

Built because of a measurement rather than a preference. In the reader benchmark,
handing an agent Loom's evidence *and* its verdict cost 10.10 points against
handing it the same evidence alone (p=0.005, and the Sortino difference survived
multiple-testing correction). The mechanism was visible too: with the verdict
present, agents used 29% fewer of the underlying findings, agreed with Loom twice
as often, and grew more confident while getting less accurate. The one unanimously
positive cell in the whole grid was evidence without a verdict, read by the
analyst persona: +5.56 points, five of five paired runs.

So the verdict is not omitted here by default. **It is unreachable.** This module
never constructs a `Brief`, which is the only object that carries a stance, and a
test asserts no response can contain one. Making it structural rather than a
filter means it cannot be reintroduced by a later endpoint forgetting to strip it.

Three other choices follow from serving an agent rather than a person.

**Raw quantities, never derived scores.** The disclosure endpoint returns the
observed and the peer-expected counts and lets the caller subtract. The residual
is one subtraction away and strictly less informative, and computing it here would
be Loom forming the judgement this API exists to leave alone.

**`as_of` on everything.** An agent evaluating its own reasoning needs to ask what
was knowable on a past date, and Loom's point-in-time discipline is the thing that
makes that answerable rather than approximate. Restatements resolve to what was on
file at the time; prices are read strictly backwards.

**Every item says how it was established.** A deterministic two-filing comparison
and a language judgement about tone are not equally trustworthy, and an agent
weighting them alike is the failure the benchmark measured. `how_established`
carries that distinction to the caller rather than burying it in a priority score.
"""

from datetime import date, datetime, time, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import CompanyRepo, DbSession
from app.api.routes.auth import CurrentUser
from app.engine.contradiction import find_contradictions
from app.engine.direction import label as direction_label
from app.engine.disclosure import (
    MIN_DOCUMENTS_FOR_GENRE,
    _document_counts,
    measure_incidence,
)
from app.models.account import User
from app.models.company import Company
from app.models.document import RawDocument
from app.models.factor import COMPOSITE_KEY, FactorScore
from app.models.signal import Signal, SignalType

router = APIRouter(prefix="/v1/evidence", tags=["evidence"])

# How each kind of finding was established, in the caller's terms.
#
# Mirrors the reasoning in engine/priority.py's type weights rather than
# re-deciding it: a risk factor's appearance is settled by comparing two filings
# and is checkable against the source text, while a shift in tone is a language
# judgement that can be wrong in ways nobody can verify. An agent that weights
# those alike is the failure the reader benchmark measured, so the distinction
# travels with the data instead of being folded into a number.
HOW_ESTABLISHED = {
    "new_risk_factor": "deterministic comparison of two filings; checkable against the source text",
    "resolved_risk_factor": "deterministic comparison of two filings; the paragraph is absent from the later one",
    "qoq_anomaly": "deterministic comparison of two filings",
    "guidance_change": "quoted from the filing, usually verbatim",
    "insider_activity": "arithmetic over filed transactions; no model involved",
    "short_interest_spike": "reported position data; no model involved",
    "emerging_pattern": "synthesised across several disclosures in a short window",
    "notable_quote": "selected from the filing; the quote is verbatim, the selection is a judgement",
    "sentiment_shift": "a language judgement about tone; the least verifiable kind here",
}

MAX_PAGE = 200


def _cutoff(as_of: Optional[date]) -> datetime:
    if as_of is None:
        return datetime.now(timezone.utc)
    return datetime.combine(as_of, time.max, tzinfo=timezone.utc)


def _signal_type(kind: str):
    """Resolve a `kind` string, rejecting an unknown one rather than filtering to
    nothing.

    An unrecognised kind returning an empty list is indistinguishable from a
    company genuinely having none of that finding, and a caller acting on the
    second reading when the first is true has been misled by a typo.
    """
    try:
        return SignalType(kind)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown kind {kind!r}. Valid kinds: "
                   f"{', '.join(sorted(t.value for t in SignalType))}.",
        )


def _company(repo, ticker: str) -> Company:
    company = repo.get_by_ticker(ticker.upper())
    if company is None:
        raise HTTPException(status_code=404, detail=f"No company {ticker.upper()}.")
    return company


# --------------------------------------------------------------- schemas


class Source(BaseModel):
    document_id: Optional[str] = None
    compared_document_id: Optional[str] = Field(
        default=None,
        description="The filing this one was compared against. For a withdrawn "
        "risk the quote's text exists only here.",
    )
    form: Optional[str] = None
    published: Optional[date] = None
    url: Optional[str] = None


class Finding(BaseModel):
    id: str
    kind: str
    direction: str = Field(
        description="What the document did: negative where a risk appeared, "
        "positive where one was withdrawn or the company's own tone improved, "
        "unassessed where the document stated no direction. This is not a "
        "prediction about the share price."
    )
    occurred_at: datetime
    summary: str
    quote: Optional[str] = Field(
        default=None, description="Verbatim from the filing. Never paraphrased."
    )
    source: Optional[Source] = Field(
        default=None,
        description="Where to go and check. Null where the finding came from "
        "transaction or position data rather than a document.",
    )
    materiality: Optional[str] = None
    confidence: Optional[float] = None
    how_established: str


class DisclosureVolume(BaseModel):
    """How much directional disclosure one document carried, against its genre.

    Both counts zero means the document was read and yielded nothing directional.
    That is not a deficit against the expectation, and subtracting it as one is
    the specific error `engine/disclosure.py` refuses by skipping these documents
    entirely: silence is not good news, and it is not bad news either. They are
    returned here rather than dropped because an agent should be able to see that
    Loom read a filing and found nothing in it — which is different from Loom not
    having read it, and only one of those is a reason to go and look.
    """

    genre: str
    document_id: str
    observed_negative: int
    observed_positive: int
    expected_negative: Optional[float] = Field(
        default=None, description="What documents of this genre and sector normally carry."
    )
    expected_positive: Optional[float] = None
    peer_documents: int = Field(
        description="How many documents the expectation was measured over. Below "
        "four the expectation is not trusted and is returned as null."
    )


class PeerRank(BaseModel):
    factor: str
    percentile: float = Field(description="0 worst, 1 best, against comparable companies.")
    as_of: date


class Dependent(BaseModel):
    """A company that named this one as something it depends on.

    The edge points inwards, which is the direction worth being careful about.
    These are not this company's suppliers — they are filers whose own 10-Ks name
    this company, so a finding here is a candidate read-across to them. The graph
    is built from what companies disclosed about each other, not from a sector
    map, so an edge means someone thought the relationship material enough to
    write down.
    """

    ticker: str
    mentions: int = Field(
        description="How often that filer's own filings name this company. "
        "Ranking only — it is a mention count, not a measure of revenue exposure."
    )


class Coverage(BaseModel):
    ticker: str
    findings: int
    documents_read: int
    periodic_filings_held: int
    newest_finding: Optional[datetime] = None
    oldest_finding: Optional[datetime] = None
    caveat: str


class EvidencePacket(BaseModel):
    ticker: str
    name: str
    as_of: date
    coverage: Coverage
    findings: list[Finding]
    contradictions: list[dict]
    disclosure_volume: list[DisclosureVolume]
    peer_ranks: list[PeerRank]
    dependents: list[Dependent]
    verdict: None = Field(
        default=None,
        description="Always null, and not a placeholder for a future field. "
        "Loom's directional verdict is deliberately not served here: in the "
        "reader benchmark it cost 10.10 points against the same evidence without "
        "it, and reduced use of the underlying findings by 29%.",
    )


# --------------------------------------------------------------- helpers


def _findings(db, company, cutoff, limit, offset, kind: Optional[str] = None) -> list[Finding]:
    """One page of findings, filtered in the query rather than after it.

    `kind` narrows the SQL deliberately. Filtering a page after it was cut would
    mean a caller asking for fifty risk factors received however many happened to
    fall inside the top fifty findings overall — three, in the case that caught
    this — while the response still reported a successful page.
    """
    query = (
        select(Signal, RawDocument)
        .outerjoin(RawDocument, RawDocument.id == Signal.source_document_id)
        .where(Signal.company_id == company.id)
        .where(Signal.occurred_at <= cutoff)
        .where(Signal.dismissed_at.is_(None))
    )
    if kind is not None:
        query = query.where(Signal.signal_type == _signal_type(kind))
    rows = db.execute(
        query
        .order_by(Signal.priority.desc().nullslast(), Signal.occurred_at.desc())
        .limit(min(limit, MAX_PAGE))
        .offset(offset)
    ).all()
    out = []
    for signal, document in rows:
        kind = str(getattr(signal.signal_type, "value", signal.signal_type))
        source = None
        if document is not None:
            source = Source(
                document_id=str(document.id),
                form=document.doc_subtype,
                published=document.published_at.date() if document.published_at else None,
                url=document.source_url,
            )
        out.append(Finding(
            id=str(signal.id),
            kind=kind,
            direction=direction_label(signal),
            occurred_at=signal.occurred_at,
            summary=signal.summary or "",
            quote=signal.evidence_quote,
            source=source,
            materiality=signal.market_magnitude,
            confidence=signal.confidence,
            how_established=HOW_ESTABLISHED.get(kind, "unspecified"),
        ))
    return out


def _coverage(db, company, cutoff) -> Coverage:
    counts = db.execute(
        select(
            func.count(Signal.id),
            func.count(func.distinct(Signal.source_document_id)),
            func.max(Signal.occurred_at),
            func.min(Signal.occurred_at),
        )
        .where(Signal.company_id == company.id)
        .where(Signal.occurred_at <= cutoff)
        .where(Signal.dismissed_at.is_(None))
    ).first()
    filings = db.execute(
        select(func.count(RawDocument.id))
        .where(RawDocument.company_id == company.id)
        .where(RawDocument.doc_subtype.in_(("10-K", "10-Q")))
    ).scalar() or 0
    read = int(counts[1] or 0)
    # Stated rather than implied. A caller cannot calibrate anything here without
    # knowing how much Loom has actually read, and a packet that looks complete
    # when it is not is the most misleading thing this endpoint could return.
    caveat = (
        "Loom has read one document for this company, so no year-over-year "
        "comparison is possible and the disclosure volume rests on a single filing."
        if read <= 1 else
        f"Loom has read {read} documents for this company."
    )
    return Coverage(
        ticker=company.ticker, findings=int(counts[0] or 0), documents_read=read,
        periodic_filings_held=int(filings), newest_finding=counts[2],
        oldest_finding=counts[3], caveat=caveat,
    )


def _disclosure_volume(db, company, cutoff) -> list[DisclosureVolume]:
    """Observed and peer-expected directional counts, per document.

    The residual is deliberately not computed. It is one subtraction away, and
    doing it here would be Loom forming the judgement this API exists to leave to
    the caller.
    """
    past = list(db.execute(
        select(Signal).where(Signal.occurred_at <= cutoff, Signal.dismissed_at.is_(None))
    ).scalars())
    sectors = {
        str(cid): sector for cid, sector in db.execute(
            select(Company.id, Company.sector).where(Company.sector.is_not(None))
        ).all()
    }
    incidence = measure_incidence(past, sectors)
    mine = [s for s in past if s.company_id == company.id]
    sector = sectors.get(str(company.id))

    out = []
    for key, entry in _document_counts(mine).items():
        expected = incidence.expected_for(entry["genre"], sector)
        # Below the floor the expectation is returned as null rather than as a
        # number, because a mean over three documents is not a peer norm and a
        # caller cannot tell the difference once it is serialised as a float.
        trusted = expected.documents >= MIN_DOCUMENTS_FOR_GENRE
        out.append(DisclosureVolume(
            genre=entry["genre"], document_id=key,
            observed_negative=entry["negative"], observed_positive=entry["positive"],
            expected_negative=round(expected.negative, 3) if trusted else None,
            expected_positive=round(expected.positive, 3) if trusted else None,
            peer_documents=expected.documents,
        ))
    return out


def _peer_ranks(db, company, as_of: date) -> list[PeerRank]:
    """Stored factor percentiles at or before the date, most recent first.

    Read from storage rather than recomputed, which keeps this endpoint cheap and
    keeps the point-in-time guarantee honest: the stored row is dated, so a caller
    asking about a past date is served the ranking that existed then.
    """
    newest = db.execute(
        select(func.max(FactorScore.as_of_date))
        .where(FactorScore.company_id == company.id)
        .where(FactorScore.as_of_date <= as_of)
    ).scalar()
    if newest is None:
        return []
    rows = db.execute(
        select(FactorScore.factor_key, FactorScore.percentile, FactorScore.as_of_date)
        .where(FactorScore.company_id == company.id)
        .where(FactorScore.as_of_date == newest)
        .where(FactorScore.percentile.is_not(None))
        .where(FactorScore.factor_key != COMPOSITE_KEY)
    ).all()
    return [
        PeerRank(factor=k, percentile=round(float(p), 4), as_of=d) for k, p, d in rows
    ]


def _dependents(db, company) -> list[Dependent]:
    """Filers that named this company as something they depend on.

    Degrades to an empty list rather than failing the packet. The dependency
    graph is built by a separate scheduled job and a company Loom has read may
    legitimately have no edges yet; losing the whole packet over an absent
    secondary section would be the wrong trade.
    """
    from app.engine.exposure import dependents_of

    try:
        edges = dependents_of(db, company.id) or []
    except Exception:
        return []
    return [
        Dependent(ticker=edge.ticker, mentions=int(edge.mention_count or 0))
        for edge in edges
    ]


# --------------------------------------------------------------- endpoints


@router.get("/capabilities")
def capabilities(user: User = CurrentUser) -> dict:
    """What this API serves, and what it deliberately withholds.

    Self-describing so an agent can discover the surface without documentation,
    and so the one deliberate omission is stated rather than left to be inferred
    from a missing field.
    """
    return {
        "serves": {
            "findings": "Extracted disclosures with verbatim quotes and provenance.",
            "changes": "Paragraphs added to and withdrawn from a filing against its predecessor.",
            "contradictions": "Where Loom's own sources disagree, with no direction assigned.",
            "disclosure_volume": "Observed and peer-expected directional counts per document.",
            "peer_ranks": "Factor percentiles against comparable companies.",
            "dependents": "Filers whose own 10-Ks name this company, as read-across candidates.",
            "coverage": "How much Loom has actually read, stated plainly.",
        },
        "withholds": {
            "verdict": (
                "Loom's directional stance is not served. In the reader benchmark, "
                "agents given the evidence plus the verdict scored 10.10 points "
                "below agents given the same evidence alone (p=0.005), used 29% "
                "fewer of the underlying findings, and grew more confident while "
                "getting less accurate. The best result in the grid was evidence "
                "without a verdict."
            ),
            "price_forecasts": (
                "Nothing here predicts a price. Direction describes what a document "
                "did, not what a market will do."
            ),
        },
        "point_in_time": (
            "Every endpoint accepts as_of=YYYY-MM-DD and returns only what was "
            "knowable then. Restatements resolve to the figure on file at the time; "
            "prices are read strictly backwards."
        ),
        "how_established": HOW_ESTABLISHED,
        "limits": {"max_page_size": MAX_PAGE},
    }


@router.get("/coverage")
def coverage_index(
    db: DbSession,
    user: User = CurrentUser,
    as_of: Annotated[Optional[date], Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 50,
) -> dict:
    """Which companies Loom has read, and how deeply."""
    cutoff = _cutoff(as_of)
    rows = db.execute(
        select(
            Company.ticker, Company.name,
            func.count(Signal.id).label("findings"),
            func.count(func.distinct(Signal.source_document_id)).label("documents"),
        )
        .join(Signal, Signal.company_id == Company.id)
        .where(Signal.occurred_at <= cutoff)
        .where(Signal.dismissed_at.is_(None))
        .group_by(Company.ticker, Company.name)
        .order_by(func.count(Signal.id).desc())
        .limit(limit)
    ).all()
    return {
        "as_of": (as_of or date.today()).isoformat(),
        "companies": [
            {"ticker": r.ticker, "name": r.name, "findings": r.findings,
             "documents_read": r.documents}
            for r in rows
        ],
        "caveat": (
            "Only companies Loom has read appear here. A company absent from this "
            "list has no document evidence, which is different from having "
            "unremarkable evidence."
        ),
    }


@router.get("/{ticker}", response_model=EvidencePacket)
def packet(
    ticker: str,
    db: DbSession,
    companies: CompanyRepo,
    user: User = CurrentUser,
    as_of: Annotated[Optional[date], Query()] = None,
    findings_limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 40,
) -> EvidencePacket:
    """Everything Loom has read about one company, with no verdict.

    One call because an agent reasoning about a company wants the whole basis at
    once rather than assembling it from six requests, and because the separate
    endpoints exist for when it wants to go deeper on one part.
    """
    company = _company(companies, ticker)
    cutoff = _cutoff(as_of)
    effective = as_of or date.today()
    signals = list(db.execute(
        select(Signal)
        .where(Signal.company_id == company.id)
        .where(Signal.occurred_at <= cutoff)
        .where(Signal.dismissed_at.is_(None))
    ).scalars())
    ranks = _peer_ranks(db, company, effective)
    percentiles = {r.factor: r.percentile for r in ranks}

    return EvidencePacket(
        ticker=company.ticker, name=company.name or company.ticker,
        as_of=effective,
        coverage=_coverage(db, company, cutoff),
        findings=_findings(db, company, cutoff, findings_limit, 0),
        # No stance is passed, so no contradiction is resolved against one. The
        # function tolerates that and returns the disagreements unranked by a
        # verdict it was not given.
        contradictions=[
            {"key": c.key, "headline": c.headline, "says_better": c.says_better,
             "says_worse": c.says_worse, "why_it_matters": c.why_it_matters,
             "plain": getattr(c, "plain", None)}
            for c in find_contradictions(signals, percentiles)
        ],
        disclosure_volume=_disclosure_volume(db, company, cutoff),
        peer_ranks=ranks,
        dependents=_dependents(db, company),
    )


@router.get("/{ticker}/findings")
def findings(
    ticker: str,
    db: DbSession,
    companies: CompanyRepo,
    user: User = CurrentUser,
    as_of: Annotated[Optional[date], Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    kind: Annotated[Optional[str], Query()] = None,
) -> dict:
    """Findings with verbatim quotes, most trustworthy and recent first."""
    company = _company(companies, ticker)
    found = _findings(db, company, _cutoff(as_of), limit, offset, kind)
    return {
        "ticker": company.ticker, "offset": offset, "returned": len(found),
        "findings": [f.model_dump(mode="json") for f in found],
    }


@router.get("/{ticker}/changes")
def changes(
    ticker: str,
    db: DbSession,
    companies: CompanyRepo,
    user: User = CurrentUser,
    as_of: Annotated[Optional[date], Query()] = None,
    section: Annotated[str, Query()] = "1A",
) -> dict:
    """What the newest filing added, and what it withdrew.

    Both directions. The withdrawn half is the only positive channel a filing
    comparison has, and for most of this engine's life it did not exist.
    """
    from app.engine.corpus import load_vocabulary
    from app.engine.diffing import diff_section
    from app.engine.pipeline import _document_text

    company = _company(companies, ticker)
    cutoff = _cutoff(as_of)
    filings = db.execute(
        select(RawDocument)
        .where(RawDocument.company_id == company.id)
        .where(RawDocument.doc_subtype == ("10-K" if section == "1A" else "10-Q"))
        .where(RawDocument.published_at <= cutoff)
        .order_by(RawDocument.published_at.desc())
        .limit(2)
    ).scalars().all()
    if len(filings) < 2:
        return {
            "ticker": company.ticker, "added": [], "withdrawn": [],
            "caveat": "Fewer than two comparable filings are held, so no "
                      "comparison is possible. This is not evidence of no change.",
        }

    vocabulary = load_vocabulary(db)
    diff = diff_section(
        _document_text(filings[0].blob_uri), _document_text(filings[1].blob_uri),
        section=section,
        rarity=vocabulary.rarity if vocabulary.measured else None,
    )

    def described(paragraphs: list[str]) -> list[dict]:
        return [
            {
                "text": p,
                "unusual_language": (
                    None if not vocabulary.measured else round(vocabulary.rarity(p) or 0.0, 3)
                ),
                "unusual_terms": vocabulary.unusual_terms(p, 5),
            }
            for p in paragraphs
        ]

    return {
        "ticker": company.ticker, "section": section,
        "current_filing": {"id": str(filings[0].id), "published": filings[0].published_at},
        "prior_filing": {"id": str(filings[1].id), "published": filings[1].published_at},
        "paragraphs_current": diff.current_total,
        "paragraphs_prior": diff.prior_total,
        "added": described(diff.added),
        "withdrawn": described(diff.removed),
        "caveat": (
            "Paragraphs here are candidates established by text comparison, not "
            "conclusions. Whether a change is substantive is a judgement, and "
            "unusual_language measures only how rare the wording is across the "
            "corpus — a paragraph written in ordinary industry language can still "
            "be the most important thing in the filing."
        ),
    }
