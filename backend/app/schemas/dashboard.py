from datetime import datetime

from pydantic import BaseModel

from app.schemas.signal import SignalWithContext


class CompanyDashboardRow(BaseModel):
    """One row of the dashboard cockpit: a company's computed state, not a
    raw record. sentiment/trend/counts are aggregation results from
    app.engine.summary, not database columns."""

    company_id: str
    ticker: str
    name: str
    sector: str | None
    sentiment_score: float | None
    sentiment_trend: float | None
    sentiment_history: list[float]
    signal_count: int
    risk_count: int
    top_signal: SignalWithContext | None
    last_signal_at: datetime | None
    bearish_count: int = 0
    bullish_count: int = 0
    major_count: int = 0
    pattern_count: int = 0
    insider_net_usd: float = 0.0
    top_priority: float = 0.0
    avg_confidence: float | None = None


class PortfolioSummaryOut(BaseModel):
    companies_total: int
    companies_covered: int
    total_risk_count: int
    avg_sentiment: float | None
    trend_up: int
    trend_down: int
    most_active_ticker: str | None
    most_active_signal_count: int


class DashboardResponse(BaseModel):
    portfolio: PortfolioSummaryOut
    companies: list[CompanyDashboardRow]
