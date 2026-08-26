"""The only module that queries `llm_usage_runs`."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.usage import LLMUsageRun


class UsageRepository:
    def __init__(self, db: Session):
        self.db = db

    def record(
        self,
        *,
        ticker: str,
        provider: str,
        model: str,
        calls: int,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        documents_analyzed: int,
    ) -> LLMUsageRun:
        run = LLMUsageRun(
            id=uuid.uuid4(),
            ticker=ticker,
            provider=provider,
            model=model,
            calls=calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            documents_analyzed=documents_analyzed,
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def list_recent(self, limit: int = 100) -> list[LLMUsageRun]:
        stmt = select(LLMUsageRun).order_by(LLMUsageRun.created_at.desc()).limit(limit)
        return list(self.db.execute(stmt).scalars().all())
