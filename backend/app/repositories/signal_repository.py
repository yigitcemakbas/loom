"""The only module that queries the `signals` and `document_analyses` tables."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.signal import AnalysisStatus, DocumentAnalysis, Signal, SignalType


class SignalRepository:
    def __init__(self, db: Session):
        self.db = db

    # ---- signals -------------------------------------------------------

    def add_all(self, signals: list[Signal]) -> list[Signal]:
        self.db.add_all(signals)
        self.db.commit()
        for signal in signals:
            self.db.refresh(signal)
        return signals

    def get_by_id(self, signal_id: uuid.UUID) -> Signal | None:
        return self.db.get(Signal, signal_id)

    def list_feed(
        self,
        *,
        company_id: uuid.UUID | None = None,
        signal_type: SignalType | None = None,
        since: datetime | None = None,
        min_confidence: float | None = None,
        include_dismissed: bool = False,
        unreviewed_only: bool = False,
        limit: int = 100,
    ) -> list[Signal]:
        """Ranked feed. Ordered by priority, not recency, the point is to
        surface what matters, not what merely happened last.

        Dismissed signals are excluded by default everywhere this is called
        (Feed, Risk Tracker, the dashboard's top-signal/expand-row), a
        dismissed finding staying visible would defeat the point of
        dismissing it. Pass include_dismissed=True only for an explicit
        "show dismissed" view if one is ever built.
        """
        stmt = select(Signal)
        if company_id is not None:
            stmt = stmt.where(Signal.company_id == company_id)
        if signal_type is not None:
            stmt = stmt.where(Signal.signal_type == signal_type)
        if since is not None:
            stmt = stmt.where(Signal.occurred_at >= since)
        if min_confidence is not None:
            stmt = stmt.where(Signal.confidence >= min_confidence)
        if not include_dismissed:
            stmt = stmt.where(Signal.dismissed_at.is_(None))
        if unreviewed_only:
            stmt = stmt.where(Signal.reviewed_at.is_(None))
        stmt = stmt.order_by(Signal.priority.desc(), Signal.occurred_at.desc()).limit(limit)
        return list(self.db.execute(stmt).scalars().all())

    def set_note(self, signal_id: uuid.UUID, note: str) -> Signal | None:
        """Writing a note is what marks a signal reviewed, a real judgment,
        not an empty acknowledgment click."""
        signal = self.get_by_id(signal_id)
        if signal is None:
            return None
        signal.note = note
        if signal.reviewed_at is None:
            signal.reviewed_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(signal)
        return signal

    def mark_dismissed(self, signal_id: uuid.UUID) -> Signal | None:
        signal = self.get_by_id(signal_id)
        if signal is None:
            return None
        signal.dismissed_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(signal)
        return signal

    def sentiment_series(self, company_id: uuid.UUID) -> list[Signal]:
        """Signals carrying a sentiment score, oldest first, for the trend chart."""
        stmt = (
            select(Signal)
            .where(Signal.company_id == company_id, Signal.sentiment_score.is_not(None))
            .order_by(Signal.occurred_at)
        )
        return list(self.db.execute(stmt).scalars().all())

    def recent_for_company(self, company_id: uuid.UUID, since: datetime) -> list[Signal]:
        """Every signal for a company since a cutoff, dismissed ones included.

        Deliberately unfiltered, unlike `list_feed`: this feeds the short-window
        clustering in engine/clustering.py, which is reasoning about what the
        company actually disclosed. Whether the reader has dismissed a card from
        their feed says nothing about whether the underlying event happened.
        """
        stmt = (
            select(Signal)
            .where(Signal.company_id == company_id, Signal.occurred_at >= since)
            .order_by(Signal.occurred_at.desc())
        )
        return list(self.db.execute(stmt).scalars().all())

    def find_pattern_by_anchor(
        self, company_id: uuid.UUID, anchor_document_id: uuid.UUID | None
    ) -> Signal | None:
        """An existing emerging-pattern signal built off the same anchor document."""
        if anchor_document_id is None:
            return None
        stmt = select(Signal).where(
            Signal.company_id == company_id,
            Signal.signal_type == SignalType.EMERGING_PATTERN,
            Signal.source_document_id == anchor_document_id,
        )
        return self.db.execute(stmt).scalars().first()

    def delete_untouched_rule_signals(self, company_id: uuid.UUID) -> int:
        """Clear rule-derived signals the user has not acted on.

        A threshold rule reports the current state of a window, so re-running it
        must replace its finding rather than stack another copy beside it.
        Annotated or dismissed signals are left alone, for the same reason as
        `delete_untouched_patterns`: regenerating something the reader already
        judged would quietly undo their decision.
        """
        stale = self.db.execute(
            select(Signal).where(
                Signal.company_id == company_id,
                Signal.signal_type.in_(
                    (SignalType.INSIDER_ACTIVITY, SignalType.SHORT_INTEREST_SPIKE)
                ),
                Signal.reviewed_at.is_(None),
                Signal.dismissed_at.is_(None),
            )
        ).scalars().all()
        for signal in stale:
            self.db.delete(signal)
        self.db.commit()
        return len(stale)

    def delete_untouched_patterns(self, company_id: uuid.UUID) -> int:
        """Clear emerging-pattern signals the user has not acted on.

        Re-synthesis should replace a stale pattern rather than stack another
        copy beside it. Signals the reader has annotated or dismissed are left
        alone: regenerating something they already judged would quietly undo
        their decision.
        """
        stale = self.db.execute(
            select(Signal).where(
                Signal.company_id == company_id,
                Signal.signal_type == SignalType.EMERGING_PATTERN,
                Signal.reviewed_at.is_(None),
                Signal.dismissed_at.is_(None),
            )
        ).scalars().all()
        for signal in stale:
            self.db.delete(signal)
        self.db.commit()
        return len(stale)

    def delete_for_document(self, document_id: uuid.UUID) -> None:
        """Clear prior signals for a document so reprocessing replaces rather
        than duplicates them."""
        for signal in self.db.execute(
            select(Signal).where(Signal.source_document_id == document_id)
        ).scalars().all():
            self.db.delete(signal)
        self.db.commit()

    # ---- analysis bookkeeping -----------------------------------------

    def list_analysis_runs(self, limit: int = 100) -> list[DocumentAnalysis]:
        """Most-recent-first history of every analysis attempt, the read
        path for the System Status page. document_analyses is populated on
        every run already; nothing displayed it until now."""
        stmt = select(DocumentAnalysis).order_by(DocumentAnalysis.created_at.desc()).limit(limit)
        return list(self.db.execute(stmt).scalars().all())

    def is_analyzed(self, document_id: uuid.UUID, prompt_version: str) -> bool:
        stmt = select(DocumentAnalysis.id).where(
            DocumentAnalysis.document_id == document_id,
            DocumentAnalysis.prompt_version == prompt_version,
            DocumentAnalysis.status == AnalysisStatus.COMPLETED,
        )
        return self.db.execute(stmt).scalar_one_or_none() is not None

    def record_analysis(
        self,
        *,
        document_id: uuid.UUID,
        prompt_version: str,
        status: AnalysisStatus,
        signal_count: int = 0,
        error: str | None = None,
    ) -> None:
        existing = self.db.execute(
            select(DocumentAnalysis).where(
                DocumentAnalysis.document_id == document_id,
                DocumentAnalysis.prompt_version == prompt_version,
            )
        ).scalar_one_or_none()

        if existing is not None:
            existing.status = status
            existing.signal_count = signal_count
            existing.error = error
        else:
            self.db.add(
                DocumentAnalysis(
                    document_id=document_id,
                    prompt_version=prompt_version,
                    status=status,
                    signal_count=signal_count,
                    error=error,
                )
            )
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
