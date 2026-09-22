"""What Loom is watching for, and what has matched it.

The fast path's whole claim is that the expensive thinking happens *before* an
event arrives, so that when a filing lands the judgement is arithmetic and
takes milliseconds. Nothing in the interface has ever shown the first half of
that: a reader could see a score appear and had no way to know what standing
expectation it was scored against, which makes the score an assertion rather
than a conclusion.

This serves the prior itself — the watch items, the consensus figures, how the
market is positioned — alongside what has since matched, so the claim can be
checked instead of taken on trust.
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import CompanyRepo, DbSession
from app.models.company import Company
from app.models.event_assessment import EventAssessment
from app.models.prior import CompanyPrior

router = APIRouter(tags=["priors"])


class WatchItemOut(BaseModel):
    topic: str
    direction: str | None = None
    severity: str | None = None
    why_it_matters: str | None = None
    # The literal phrases the fast path matches against. Shown because they
    # are what turns "Loom noticed this" into something a reader can verify.
    keywords: list[str] = []


class PriorOut(BaseModel):
    ticker: str
    summary: str
    generated_at: datetime
    watch_items: list[WatchItemOut]
    expectations: dict
    positioning: dict
    already_priced: list
    source_signal_count: int
    # How many filings have been scored against this prior since it was built,
    # and how many cleared the notability bar.
    matched: int
    notable: int


class PriorSummaryOut(BaseModel):
    """One row of the coverage table.

    Companies without a prior are included rather than filtered out. A watched
    company with no prior scores every filing zero, which is indistinguishable
    from a quiet week, and hiding that would make the coverage gap invisible.
    """

    ticker: str
    name: str
    has_prior: bool
    generated_at: datetime | None = None
    watch_item_count: int = 0
    matched: int = 0
    notable: int = 0


def _counts(db, company_id) -> tuple[int, int]:
    rows = list(db.execute(
        select(EventAssessment.score).where(EventAssessment.company_id == company_id)
    ).scalars())
    return len(rows), sum(1 for score in rows if score >= 1.0)


@router.get("/priors", response_model=list[PriorSummaryOut])
def list_priors(db: DbSession, only_armed: bool = Query(False)):
    """Coverage across the universe: who is armed and who is mute."""
    companies = list(db.execute(select(Company).order_by(Company.ticker)).scalars())

    latest: dict = {}
    for prior in db.execute(
        select(CompanyPrior).order_by(CompanyPrior.company_id, CompanyPrior.generated_at)
    ).scalars():
        latest[prior.company_id] = prior

    out: list[PriorSummaryOut] = []
    for company in companies:
        prior = latest.get(company.id)
        if prior is None and only_armed:
            continue
        matched, notable = _counts(db, company.id) if prior else (0, 0)
        out.append(PriorSummaryOut(
            ticker=company.ticker, name=company.name,
            has_prior=prior is not None,
            generated_at=prior.generated_at if prior else None,
            watch_item_count=len(prior.watch_items or []) if prior else 0,
            matched=matched, notable=notable,
        ))
    # Armed companies first, then by how much has actually matched: a prior
    # nothing has tested is a claim, not a record.
    out.sort(key=lambda r: (r.has_prior, r.notable, r.matched), reverse=True)
    return out


@router.get("/companies/{ticker}/prior", response_model=PriorOut)
def company_prior(ticker: str, company_repo: CompanyRepo, db: DbSession):
    company = company_repo.get_by_ticker(ticker.upper())
    if company is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker {ticker!r}")

    prior = db.execute(
        select(CompanyPrior)
        .where(CompanyPrior.company_id == company.id)
        .order_by(CompanyPrior.generated_at.desc())
        .limit(1)
    ).scalars().first()
    if prior is None:
        raise HTTPException(
            status_code=404,
            detail=f"Loom has not built a standing view for {company.ticker} yet.",
        )

    matched, notable = _counts(db, company.id)
    return PriorOut(
        ticker=company.ticker,
        summary=prior.summary,
        generated_at=prior.generated_at,
        watch_items=[
            WatchItemOut(
                topic=item.get("topic", ""),
                direction=item.get("direction"),
                severity=item.get("severity"),
                why_it_matters=item.get("why_it_matters"),
                keywords=item.get("keywords", []),
            )
            for item in (prior.watch_items or [])
        ],
        expectations=prior.expectations or {},
        positioning=prior.positioning or {},
        already_priced=prior.already_priced or [],
        source_signal_count=prior.source_signal_count,
        matched=matched, notable=notable,
    )
