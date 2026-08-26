"""Earnings timing and expectations, the event layer over stored facts."""

from fastapi import APIRouter, HTTPException

from app.api.deps import CompanyRepo, DbSession, FactRepo, WatchlistRepo
from app.engine.earnings import build_outlook
from app.schemas.earnings import EarningsOutlookOut

router = APIRouter(tags=["earnings"])


def _outlook_for(company, fact_repo) -> EarningsOutlookOut:
    events = fact_repo.latest_per_date(fact_repo.earnings_events(company.id))
    outlook = build_outlook(events)
    return EarningsOutlookOut(ticker=company.ticker, **outlook.__dict__)


@router.get("/companies/{ticker}/earnings", response_model=EarningsOutlookOut)
def company_earnings(ticker: str, company_repo: CompanyRepo, fact_repo: FactRepo):
    company = company_repo.get_by_ticker(ticker)
    if company is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker {ticker!r}")
    return _outlook_for(company, fact_repo)


@router.get("/earnings/upcoming", response_model=list[EarningsOutlookOut])
def upcoming_earnings(watchlist_repo: WatchlistRepo, fact_repo: FactRepo, db: DbSession):
    """Every tracked company's next report, soonest first.

    Ordering is the feature: the company reporting tonight is the one whose
    evidence a reader needs now, and it should not be buried alphabetically.
    """
    from app.repositories.watchlist_repository import WatchlistRepository

    watchlist = WatchlistRepository(db).get_or_create_default()
    out = [
        _outlook_for(company, fact_repo)
        for company in watchlist_repo.list_companies(watchlist.id)
    ]
    out.sort(key=lambda o: (o.days_until is None, o.days_until if o.days_until is not None else 0))
    return out
