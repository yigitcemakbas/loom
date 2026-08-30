from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.api.deps import CompanyRepo, WatchlistRepo
from app.schemas.company import CompanyCreate, CompanyOut
from app.schemas.watchlist import AddTickerRequest, WatchlistCreate, WatchlistOut
from app.scheduling.jobs import run_initial_ingest
from app.services.company_lookup import SecAccessDenied, get_company_lookup_service

router = APIRouter(prefix="/watchlists", tags=["watchlists"])


@router.get("", response_model=list[WatchlistOut])
def list_watchlists(watchlist_repo: WatchlistRepo):
    # A brand-new install has no watchlist yet. Auto-creating one here
    # (rather than requiring a seed script) means the app is usable the
    # moment the API and frontend are up, with no CLI step in between.
    watchlists = watchlist_repo.list_all()
    if not watchlists:
        watchlists = [watchlist_repo.get_or_create_default()]
    return watchlists


@router.post("", response_model=WatchlistOut)
def create_watchlist(data: WatchlistCreate, watchlist_repo: WatchlistRepo):
    return watchlist_repo.create(data)


@router.get("/{watchlist_id}/items", response_model=list[CompanyOut])
def list_watchlist_items(watchlist_id: str, watchlist_repo: WatchlistRepo):
    return watchlist_repo.list_companies(watchlist_id)


@router.post("/{watchlist_id}/items", response_model=list[CompanyOut])
def add_ticker(
    watchlist_id: str,
    data: AddTickerRequest,
    background_tasks: BackgroundTasks,
    watchlist_repo: WatchlistRepo,
    company_repo: CompanyRepo,
):
    """Add any real ticker, not just ones already known to Loom.

    An unknown ticker is resolved on the fly against SEC's public ticker
    directory (the same one the SEC EDGAR adapter uses), created, and
    queued for an immediate background ingest, so filings start showing
    up without a separate CLI step.
    """
    ticker = data.ticker.upper()
    company = company_repo.get_by_ticker(ticker)
    is_new_company = company is None

    if company is None:
        try:
            info = get_company_lookup_service().lookup(ticker)
        except SecAccessDenied as exc:
            # 503 rather than 404: nothing is wrong with the ticker, the app
            # cannot reach SEC at all. Reporting this as "unrecognised ticker"
            # sent readers hunting for a typo in "AAPL".
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if info is None:
            raise HTTPException(status_code=404, detail=f"{ticker!r} isn't a recognized ticker.")
        company = company_repo.create(
            CompanyCreate(ticker=info.ticker, name=info.name, cik=info.cik)
        )

    watchlist_repo.add_company(watchlist_id, company.id)

    if is_new_company:
        background_tasks.add_task(run_initial_ingest, ticker)

    return watchlist_repo.list_companies(watchlist_id)


@router.delete("/{watchlist_id}/items/{company_id}", response_model=list[CompanyOut])
def remove_ticker(watchlist_id: str, company_id: str, watchlist_repo: WatchlistRepo):
    watchlist_repo.remove_company(watchlist_id, company_id)
    return watchlist_repo.list_companies(watchlist_id)
