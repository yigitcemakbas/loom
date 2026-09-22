"""Routes for the product's headline output.

Stance and source names are translated to plain language here rather than in
each client, so "10-Q" and "strong_negative" never reach a screen.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import BriefRepo, CompanyRepo, DbSession, WatchlistRepo
from app.engine.brief import HORIZONS, SOURCE_LABELS, STANCE_LABELS, build_brief
from app.engine.pipeline import regenerate_brief
from app.repositories.signal_repository import SignalRepository
from app.schemas.brief import BriefOut

router = APIRouter(tags=["briefs"])


def _to_out(brief) -> BriefOut:
    return BriefOut(
        id=brief.id,
        company_id=brief.company_id,
        stance=brief.stance,
        stance_label=STANCE_LABELS.get(brief.stance, brief.stance.value),
        headline=brief.headline,
        confidence=brief.confidence,
        drivers=brief.drivers or [],
        counterpoint=brief.counterpoint,
        what_changed=brief.what_changed,
        source_types=brief.source_types or [],
        source_labels=[SOURCE_LABELS.get(s, s) for s in (brief.source_types or [])],
        signal_count=brief.signal_count,
        evidence=brief.evidence or {},
        generated_at=brief.generated_at,
    )


@router.get("/companies/{ticker}/brief", response_model=BriefOut)
def get_brief(ticker: str, company_repo: CompanyRepo, brief_repo: BriefRepo, db: DbSession):
    """The current read for one company.

    Generated on demand when absent: the brief is cheap (no model call) and a
    reader arriving at a company page should never be shown an empty screen
    just because no analysis batch has run since the feature shipped.
    """
    company = company_repo.get_by_ticker(ticker)
    if company is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker {ticker!r}")

    brief = brief_repo.latest_for(company.id) or regenerate_brief(ticker, db)
    if brief is None:
        raise HTTPException(status_code=404, detail="No brief available")
    return _to_out(brief)


@router.get("/companies/{ticker}/brief/horizon", response_model=BriefOut)
def brief_for_horizon(
    ticker: str,
    company_repo: CompanyRepo,
    db: DbSession,
    horizon: str = Query(description="One of: " + ", ".join(HORIZONS)),
):
    """The same company judged over a stated holding period.

    Computed on request rather than stored, because a brief is pure arithmetic
    over findings already in the database and caching four variants of every
    company would mean four things to keep fresh instead of one.

    The answer genuinely differs by horizon rather than being the same verdict
    relabelled: a finding the extraction marked near-term barely counts toward
    a five-year view, and a structural one barely counts toward a week. Where
    the recent record is too thin to support a short horizon, the response says
    so rather than reaching a confident verdict from two stale findings.
    """
    if horizon not in HORIZONS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown horizon {horizon!r}. Expected one of {', '.join(HORIZONS)}.",
        )

    company = company_repo.get_by_ticker(ticker)
    if company is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker {ticker!r}")

    signals = SignalRepository(db).list_feed(company_id=company.id, limit=500)
    result = build_brief(signals, horizon=horizon)

    # Shaped like a stored brief so the client renders one component either
    # way, but never written: this is a view of the evidence, not a new record.
    return BriefOut(
        id=company.id,
        company_id=company.id,
        stance=result.stance,
        stance_label=STANCE_LABELS.get(result.stance, result.stance.value),
        headline=result.headline,
        confidence=result.confidence,
        # The engine returns dataclasses; a stored brief holds the same shape
        # as JSON. Converting here keeps the response identical either way, so
        # the client renders one component regardless of which path produced it.
        drivers=[d.__dict__ for d in (result.drivers or [])],
        counterpoint=result.counterpoint.__dict__ if result.counterpoint else None,
        what_changed=result.what_changed,
        source_types=result.source_types or [],
        source_labels=[SOURCE_LABELS.get(s, s) for s in (result.source_types or [])],
        signal_count=result.signal_count,
        evidence=result.evidence or {},
        generated_at=datetime.now(timezone.utc),
    )


@router.post("/companies/{ticker}/brief/refresh", response_model=BriefOut)
def refresh_brief(ticker: str, company_repo: CompanyRepo, db: DbSession):
    company = company_repo.get_by_ticker(ticker)
    if company is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker {ticker!r}")

    brief = regenerate_brief(ticker, db)
    if brief is None:
        raise HTTPException(status_code=404, detail="No brief available")
    return _to_out(brief)


@router.get("/briefs", response_model=list[BriefOut])
def list_briefs(
    watchlist_repo: WatchlistRepo,
    brief_repo: BriefRepo,
    db: DbSession,
):
    """Every tracked company's current read, worst first.

    Ordering is the point of this endpoint: a reader opening the app wants the
    companies needing attention at the top, not an alphabetical list.
    """
    from app.repositories.watchlist_repository import WatchlistRepository

    watchlist = WatchlistRepository(db).get_or_create_default()
    out: list[BriefOut] = []
    for company in watchlist_repo.list_companies(watchlist.id):
        brief = brief_repo.latest_for(company.id) or regenerate_brief(company.ticker, db)
        if brief is not None:
            out.append(_to_out(brief))

    # Most negative and most confident first; "no view" sinks to the bottom
    # because it is the one state that needs no decision from the reader.
    severity = {
        "strong_negative": 0, "negative": 1, "mixed": 2,
        "positive": 3, "strong_positive": 4, "quiet": 5, "insufficient": 6,
    }
    out.sort(key=lambda b: (severity.get(b.stance.value, 9), -b.confidence))
    return out
