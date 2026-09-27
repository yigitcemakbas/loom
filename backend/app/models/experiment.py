"""Recording what an agent decided, and why, so it can be scored later.

Built for one question: does Loom help somebody make a better decision? The
design is a paired trial — a professional, an amateur and a gambler, each run
twice, once with Loom and once without — so what is stored has to support the
comparison rather than just log activity.

Three things follow from that.

**The reasoning is the primary record, not the trade.** Six agents making a
handful of decisions can never produce a return result that clears any
statistical bar; the sample is hopeless by construction and no amount of
patience fixes it. What six agents CAN produce is reasoning a grader can
assess blind: did this decision rest on something real, was the risk
identified, was the evidence cited or invented. So `rationale` is required and
the trade is almost incidental.

**Which Loom facts were used is recorded separately from the reasoning.**
An agent saying "the accruals looked bad" is a claim; the case-file points it
actually consulted are a record. Without the second, a favourable result cannot
be attributed to Loom rather than to the agent already knowing the company.

**Everything is stamped with what was knowable.** The price at decision and the
Loom snapshot are stored on the row, because scoring a decision months later
against evidence Loom has since revised would measure the revision rather than
the decision.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# The personas under test. Stored as plain strings rather than an enum so a
# trial can add one without a migration; the analysis groups on whatever is
# present.
COHORTS = ("professional", "amateur", "gambler")

# What an agent can decide. "pass" is deliberately available and deliberately
# recorded: declining to trade is a decision, and a tool that stops somebody
# buying something bad has helped them even though nothing was bought.
ACTIONS = ("buy", "sell", "short", "pass")


class AgentDecision(Base):
    """One decision by one agent, with its reasoning and what it was shown."""

    __tablename__ = "agent_decisions"
    __table_args__ = (
        Index("ix_agent_decisions_run_cohort", "run_id", "cohort"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Groups the six agents of one trial, so several trials can coexist and be
    # compared without mixing.
    run_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    cohort: Mapped[str] = mapped_column(String, nullable=False)
    # The arm of the pair. The whole experiment is this one boolean.
    used_loom: Mapped[bool] = mapped_column(Boolean, nullable=False)
    agent_label: Mapped[str | None] = mapped_column(String, nullable=True)

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String, nullable=False)
    # A fraction of the agent's budget rather than a share count, so cohorts
    # with different notional capital stay comparable.
    weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    # The agent's own confidence, 0 to 1. Stored so a wrong confident call can
    # cost more than a wrong tentative one, the same way verdicts are scored.
    conviction: Mapped[float | None] = mapped_column(Float, nullable=True)

    # The primary record. Required at the API, because a decision without a
    # reason cannot be graded and is the thing this experiment exists to read.
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    # What the agent said would change its mind. Present because an unfalsifiable
    # thesis is a different quality of reasoning from one with a stated test.
    would_change_mind: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Which Loom points were actually consulted, by key. Distinguishes "the
    # agent had Loom" from "the agent used Loom", which is the difference
    # between a correlation and an attribution.
    loom_points_used: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # The verdict, factor readings and contradictions as they stood at decision
    # time. Scoring against a later revision would measure the revision.
    loom_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    price_at_decision: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Filled by the scorer, not the agent.
    forward_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    benchmark_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    abnormal_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    scored_sessions: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    @property
    def direction(self) -> float:
        """Which way the decision leans, for scoring. A pass has no direction
        and is scored on whether it avoided a loss, not on a return."""
        if self.action == "buy":
            return 1.0
        if self.action in ("sell", "short"):
            return -1.0
        return 0.0
