"""What moved since you last looked.

The feed that turns Loom from something you consult into something that tells
you when to consult it.
"""

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.api.deps import DbSession
from app.engine.changes import Change, recent_changes

router = APIRouter(tags=["changes"])


class ChangeOut(BaseModel):
    ticker: str
    kind: str
    headline: str
    detail: str
    occurred_at: str
    factor_key: str | None = None


class ChangesOut(BaseModel):
    days: int
    changes: list[ChangeOut]


@router.get("/changes", response_model=ChangesOut)
def list_changes(
    db: DbSession,
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(60, ge=1, le=200),
):
    found: list[Change] = recent_changes(db, days=days, limit=limit)
    return ChangesOut(
        days=days,
        changes=[
            ChangeOut(
                ticker=c.ticker, kind=c.kind, headline=c.headline,
                detail=c.detail, occurred_at=c.occurred_at.isoformat(),
                factor_key=c.factor_key,
            )
            for c in found
        ],
    )
