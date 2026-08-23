from fastapi import APIRouter, HTTPException

from app.api.deps import CompanyRepo, WatchlistRepo
from app.schemas.company import CompanyOut
from app.schemas.watchlist import AddTickerRequest, WatchlistCreate, WatchlistOut

router = APIRouter(prefix="/watchlists", tags=["watchlists"])


@router.get("", response_model=list[WatchlistOut])
def list_watchlists(watchlist_repo: WatchlistRepo):
    return watchlist_repo.list_all()


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
    watchlist_repo: WatchlistRepo,
    company_repo: CompanyRepo,
):
    company = company_repo.get_by_ticker(data.ticker)
    if company is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown ticker {data.ticker!r} — seed it before adding to a watchlist.",
        )
    watchlist_repo.add_company(watchlist_id, company.id)
    return watchlist_repo.list_companies(watchlist_id)


@router.delete("/{watchlist_id}/items/{company_id}", response_model=list[CompanyOut])
def remove_ticker(watchlist_id: str, company_id: str, watchlist_repo: WatchlistRepo):
    watchlist_repo.remove_company(watchlist_id, company_id)
    return watchlist_repo.list_companies(watchlist_id)
