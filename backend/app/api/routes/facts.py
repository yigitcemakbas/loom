"""Thin routes over FactRepository. No business logic here."""

from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import CompanyRepo, FactRepo
from app.models.structured_fact import FactType
from app.schemas.fact import InsiderActivitySummary, StructuredFactOut

router = APIRouter(tags=["facts"])

# Matches the insider-cluster rule's reasoning window, extended enough to give
# the panel some history to show rather than a single fortnight.
DEFAULT_ACTIVITY_DAYS = 90


@router.get("/companies/{ticker}/facts", response_model=list[StructuredFactOut])
def list_company_facts(
    ticker: str,
    company_repo: CompanyRepo,
    fact_repo: FactRepo,
    fact_type: FactType | None = None,
    days: int = Query(default=DEFAULT_ACTIVITY_DAYS, ge=1, le=1825),
    limit: int = Query(default=200, le=500),
):
    """Per-ticker fact series, most recent first."""
    company = company_repo.get_by_ticker(ticker)
    if company is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker {ticker!r}")

    return fact_repo.list_for_company(
        company.id,
        fact_type=fact_type,
        since=date.today() - timedelta(days=days),
        limit=limit,
    )


@router.get("/companies/{ticker}/insider-activity", response_model=InsiderActivitySummary)
def insider_activity(
    ticker: str,
    company_repo: CompanyRepo,
    fact_repo: FactRepo,
    days: int = Query(default=DEFAULT_ACTIVITY_DAYS, ge=1, le=1825),
):
    """Rollup of a company's recent insider transactions.

    Open-market figures are kept separate from the raw transaction count on
    purpose. Most Form 4 activity is tax withholding on vesting shares and
    option exercises, so a single headline number would overstate what insiders
    actually decided to do, which is the standard way this data misleads.
    """
    company = company_repo.get_by_ticker(ticker)
    if company is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker {ticker!r}")

    facts = fact_repo.list_for_company(
        company.id,
        fact_type=FactType.INSIDER_TRANSACTION,
        since=date.today() - timedelta(days=days),
        limit=500,
    )

    open_market = [f for f in facts if (f.attributes or {}).get("is_open_market")]
    sold = sum(
        float((f.attributes or {}).get("value_usd") or 0)
        for f in open_market
        if (f.attributes or {}).get("disposed")
    )
    bought = sum(
        float((f.attributes or {}).get("value_usd") or 0)
        for f in open_market
        if not (f.attributes or {}).get("disposed")
    )
    insiders = {(f.attributes or {}).get("owner") for f in facts}
    insiders.discard(None)

    return InsiderActivitySummary(
        transactions=len(facts),
        open_market_transactions=len(open_market),
        open_market_sold_usd=round(sold, 2),
        open_market_bought_usd=round(bought, 2),
        distinct_insiders=len(insiders),
        latest_transaction_date=max((f.as_of_date for f in facts), default=None),
    )
