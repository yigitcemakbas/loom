"""Optional convenience script: pre-populates AAPL and MSFT for local
development. Not required to use Loom — the default watchlist is created
automatically on first use, and any ticker can be added directly from the
UI (it is resolved and ingested on the fly, see
app/api/routes/watchlists.py). This just saves typing two tickers by hand
when starting from a fresh database.

Usage: python -m scripts.seed_companies
"""

from app.db.session import SessionLocal
from app.repositories.company_repository import CompanyRepository
from app.repositories.watchlist_repository import WatchlistRepository
from app.schemas.company import CompanyCreate

SEED_COMPANIES = [
    CompanyCreate(ticker="AAPL", name="Apple Inc.", sector="Technology", exchange="NASDAQ"),
    CompanyCreate(ticker="MSFT", name="Microsoft Corporation", sector="Technology", exchange="NASDAQ"),
]


def main() -> None:
    db = SessionLocal()
    try:
        company_repo = CompanyRepository(db)
        watchlist_repo = WatchlistRepository(db)

        watchlist = watchlist_repo.get_or_create_default()
        print(f"Using watchlist: {watchlist.name} ({watchlist.id})")

        for data in SEED_COMPANIES:
            company = company_repo.get_or_create(data)
            watchlist_repo.add_company(watchlist.id, company.id)
            print(f"  seeded {company.ticker} — {company.name}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
