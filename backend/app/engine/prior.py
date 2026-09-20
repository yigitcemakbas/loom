"""Builds the standing prior: the slow half of the fast path.

This is the only module in the engine that runs with no event in front of it.
Everything it produces is a preparation for work that has not arrived yet,
which is precisely why it is allowed to be slow and expensive: it never sits
between an event and a decision.

The division of labour inside it is deliberate. Positioning and price context
are arithmetic and are computed here directly; only the judgement of *what is
worth watching* goes to a model, and it goes once per company rather than once
per event. See `engine/reaction.py` for the half that has to be fast.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.engine.earnings import build_outlook
from app.engine.llm_client import LLMClient, get_llm_client
from app.engine.market_context import context_for
from app.engine.prompts import standing_prior
from app.engine.prompts.standing_prior import StandingPriorResult
from app.models.company import CompanyTier
from app.models.structured_fact import FactType
from app.repositories.company_repository import CompanyRepository
from app.repositories.fact_repository import FactRepository
from app.repositories.prior_repository import PriorRepository
from app.repositories.signal_repository import SignalRepository

logger = logging.getLogger(__name__)

ENGINE_VERSION = "2026-09-20.1"

# How much recent evidence the prior is built from.
WINDOW_DAYS = 120
MAX_SIGNALS = 40

# Above this, an unwind has to buy stock back and a surprise lands harder.
CROWDED_DAYS_TO_COVER = 5.0

# A move of at least this size means the market has visibly reacted already.
PRICED_IN_MOVE_PERCENT = 5.0


@dataclass
class Positioning:
    """How the market is set up going in. Entirely arithmetic."""

    days_to_cover: Optional[float] = None
    short_crowded: bool = False
    short_change_percent: Optional[float] = None
    insider_net_usd: float = 0.0
    position_in_52w_range: Optional[float] = None
    change_1m: Optional[float] = None
    change_3m: Optional[float] = None

    def as_dict(self) -> dict:
        return {
            "days_to_cover": self.days_to_cover,
            "short_crowded": self.short_crowded,
            "short_change_percent": self.short_change_percent,
            "insider_net_usd": self.insider_net_usd,
            "position_in_52w_range": self.position_in_52w_range,
            "change_1m": self.change_1m,
            "change_3m": self.change_3m,
        }


def _positioning(company_id, ticker: str, fact_repo: FactRepository) -> Positioning:
    """Read the numeric setup. No model, no judgement, no network beyond prices."""
    out = Positioning()

    shorts = fact_repo.list_for_company(company_id, fact_type=FactType.SHORT_INTEREST, limit=1)
    if shorts:
        attrs = shorts[0].attributes or {}
        dtc = attrs.get("days_to_cover")
        if dtc is not None:
            try:
                out.days_to_cover = float(dtc)
                out.short_crowded = out.days_to_cover >= CROWDED_DAYS_TO_COVER
            except (TypeError, ValueError):
                pass
        change = attrs.get("change_percent")
        if change is not None:
            try:
                out.short_change_percent = float(change)
            except (TypeError, ValueError):
                pass

    since = date.today() - timedelta(days=90)
    insider = fact_repo.list_for_company(
        company_id, fact_type=FactType.INSIDER_TRANSACTION, since=since, limit=200
    )
    net = 0.0
    for fact in insider:
        attrs = fact.attributes or {}
        if not attrs.get("is_open_market"):
            continue
        value = attrs.get("value_usd") or 0.0
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        net += -value if attrs.get("disposed") else value
    out.insider_net_usd = round(net, 2)

    context = context_for(ticker)
    if context is not None:
        out.position_in_52w_range = context.position_in_range
        out.change_1m = context.change_1m
        out.change_3m = context.change_3m

    return out


def _evidence_block(signals, outlook, positioning: Positioning, ticker: str) -> str:
    """The material the model reasons over. Assembled, not summarised."""
    lines: list[str] = [f"COMPANY: {ticker}", ""]

    lines.append("RECENT FINDINGS (most important first):")
    for s in signals:
        bits = [s.summary]
        if s.market_direction:
            bits.append(f"[{s.market_direction}/{s.market_magnitude or 'unrated'}]")
        if s.evidence_quote:
            bits.append(f'quote: "{s.evidence_quote[:300]}"')
        lines.append(f"- {' '.join(bits)}")
    if not signals:
        lines.append("- (none recorded)")

    lines.append("")
    lines.append("WHAT THE MARKET EXPECTS:")
    if outlook.next_date:
        lines.append(
            f"- Next report {outlook.next_date} ({outlook.quarter_label or 'quarter unknown'}), "
            f"{outlook.when_label or 'timing unknown'}"
        )
    if outlook.eps_estimate is not None:
        lines.append(f"- Consensus EPS {outlook.eps_estimate}")
    if outlook.revenue_estimate is not None:
        lines.append(f"- Consensus revenue {outlook.revenue_estimate}")

    lines.append("")
    lines.append("HOW THE MARKET IS POSITIONED:")
    if positioning.days_to_cover is not None:
        lines.append(
            f"- {positioning.days_to_cover:.1f} days to cover"
            f"{', a crowded short' if positioning.short_crowded else ''}"
        )
    if positioning.position_in_52w_range is not None:
        lines.append(
            f"- Trading at {positioning.position_in_52w_range * 100:.0f}% of its 52 week range"
        )
    if positioning.change_1m is not None:
        lines.append(f"- {positioning.change_1m:+.1f}% over the past month")
    if positioning.change_3m is not None:
        lines.append(f"- {positioning.change_3m:+.1f}% over the past three months")
    if positioning.insider_net_usd:
        lines.append(f"- Net open-market insider flow {positioning.insider_net_usd:+,.0f} USD")

    return "\n".join(lines)


def _already_priced(topics: list[str], positioning: Positioning) -> list[dict]:
    """Attach the move that justifies calling something already priced.

    A topic the model believes is priced in, with no visible move behind it, is
    an opinion. With a move behind it, it is an observation.
    """
    moved = positioning.change_1m
    if moved is None or abs(moved) < PRICED_IN_MOVE_PERCENT:
        return []
    return [
        {"topic": topic, "moved_percent": moved, "window": "1m"}
        for topic in topics
    ]


def build_prior(ticker: str, db: Session, client: Optional[LLMClient] = None):
    """Work out, in advance, what would matter about this company.

    Returns the stored CompanyPrior, or None when there is nothing to build one
    from. Focus tier only: the wide tier deliberately never reaches a model.
    """
    company_repo = CompanyRepository(db)
    company = company_repo.get_by_ticker(ticker)
    if company is None:
        raise ValueError(f"No company found for ticker {ticker!r}, seed it first.")

    if company.tier != CompanyTier.FOCUS:
        logger.info("Prior skipped for %s: wide tier companies do not get model calls.", ticker)
        return None

    signal_repo = SignalRepository(db)
    fact_repo = FactRepository(db)

    signals = signal_repo.list_feed(
        company_id=company.id,
        since=datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS),
        limit=MAX_SIGNALS,
    )

    events = fact_repo.latest_per_date(fact_repo.earnings_events(company.id))
    outlook = build_outlook(events)
    positioning = _positioning(company.id, company.ticker, fact_repo)

    if not signals and outlook.next_date is None:
        logger.info("Prior skipped for %s: no findings and no scheduled report.", ticker)
        return None

    client = client or get_llm_client()
    result: StandingPriorResult | None = client.parse(
        system=standing_prior.SYSTEM,
        user_content=_evidence_block(signals, outlook, positioning, company.ticker),
        schema=StandingPriorResult,
    )
    if result is None:
        logger.warning("Prior generation returned nothing for %s.", ticker)
        return None

    watch_items = [
        {
            "topic": item.topic,
            # Lowercased once here so the fast path never has to.
            "keywords": [k.lower().strip() for k in item.keywords if k.strip()],
            "watch_for": item.watch_for,
            "direction_if_confirmed": item.direction_if_confirmed,
            "severity": item.severity,
            "why_it_matters": item.why_it_matters,
            "evidence_quote": item.evidence_quote,
        }
        for item in result.watch_items
    ]

    expectations = {
        "eps_estimate": outlook.eps_estimate,
        "revenue_estimate": outlook.revenue_estimate,
        "next_report_date": outlook.next_date.isoformat() if outlook.next_date else None,
        "quarter_label": outlook.quarter_label,
        "guidance_notes": result.guidance_notes,
    }

    return PriorRepository(db).create(
        company_id=company.id,
        summary=result.summary,
        watch_items=watch_items,
        expectations=expectations,
        positioning=positioning.as_dict(),
        already_priced=_already_priced(result.already_priced, positioning),
        source_signal_count=len(signals),
        prompt_version=standing_prior.PROMPT_VERSION,
        engine_version=ENGINE_VERSION,
    )
