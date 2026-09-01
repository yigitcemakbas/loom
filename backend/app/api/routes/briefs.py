"""Routes for the product's headline output.

Stance and source names are translated to plain language here rather than in
each client, so "10-Q" and "strong_negative" never reach a screen.
"""

from fastapi import APIRouter, HTTPException

from app.api.deps import BriefRepo, CompanyRepo, DbSession, WatchlistRepo
from app.engine.brief import SOURCE_LABELS, STANCE_LABELS
from app.engine.pipeline import regenerate_brief
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
