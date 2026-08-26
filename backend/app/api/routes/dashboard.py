"""The dashboard: a portfolio-level read plus one computed row per tracked
company, not a document list.

Route stays thin, all synthesis logic lives in app.engine.summary. This is
what turns "here are some signals" into "here is your portfolio's current
state," which is the actual point of the product.
"""

from fastapi import APIRouter

from datetime import date, timedelta

from app.api.deps import (
    CompanyRepo,
    DbSession,
    DocumentRepo,
    FactRepo,
    SignalRepo,
    WatchlistRepo,
)
from app.api.routes.signals import with_context
from app.engine.summary import CompanySummary, summarize, summarize_portfolio
from app.repositories.watchlist_repository import WatchlistRepository
from app.schemas.dashboard import CompanyDashboardRow, DashboardResponse, PortfolioSummaryOut

router = APIRouter(tags=["dashboard"])

# Insider window for the screener's net-flow column. Matches the activity
# panel on the company page so the two never disagree.
INSIDER_WINDOW_DAYS = 90


@router.get("/dashboard", response_model=DashboardResponse)
def get_dashboard(
    db: DbSession,
    watchlist_repo: WatchlistRepo,
    company_repo: CompanyRepo,
    document_repo: DocumentRepo,
    signal_repo: SignalRepo,
    fact_repo: FactRepo,
):
    watchlist = WatchlistRepository(db).get_or_create_default()
    companies = watchlist_repo.list_companies(watchlist.id)

    rows: list[CompanyDashboardRow] = []
    summaries: list[tuple[str, CompanySummary]] = []
    for company in companies:
        all_signals = signal_repo.list_feed(company_id=company.id, limit=500)
        sentiment_series = signal_repo.sentiment_series(company.id)
        facts = fact_repo.list_for_company(
            company.id, since=date.today() - timedelta(days=INSIDER_WINDOW_DAYS), limit=500
        )
        summary = summarize(all_signals, sentiment_series, facts)
        summaries.append((company.ticker, summary))

        top_with_context = (
            with_context([summary.top_signal], company_repo, document_repo)[0]
            if summary.top_signal
            else None
        )

        rows.append(
            CompanyDashboardRow(
                company_id=str(company.id),
                ticker=company.ticker,
                name=company.name,
                sector=company.sector,
                sentiment_score=summary.sentiment_score,
                sentiment_trend=summary.sentiment_trend,
                sentiment_history=summary.sentiment_history,
                signal_count=summary.signal_count,
                risk_count=summary.risk_count,
                top_signal=top_with_context,
                last_signal_at=summary.last_signal_at,
                bearish_count=summary.bearish_count,
                bullish_count=summary.bullish_count,
                major_count=summary.major_count,
                pattern_count=summary.pattern_count,
                insider_net_usd=summary.insider_net_usd,
                top_priority=summary.top_priority,
                avg_confidence=summary.avg_confidence,
            )
        )

    # Most urgent portfolio state first: highest risk count, then most
    # negative sentiment, so the row that most needs attention is on top.
    rows.sort(key=lambda r: (-r.risk_count, r.sentiment_score or 0))

    portfolio = summarize_portfolio(summaries)
    return DashboardResponse(portfolio=PortfolioSummaryOut(**portfolio.__dict__), companies=rows)
