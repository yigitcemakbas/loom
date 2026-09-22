"""Oversight of the instance: who is on it, and what the engine is doing.

Deliberately narrow. Running the engine, adding companies and reading
everything Loom produces are the product, and every account can do them
without asking anybody. What is behind this router is the pair of things a
person should not see by default on an instance somebody else also uses: other
people's accounts, and the engine's own internals.

Nothing here can change an account's admin status. That comes from
configuration and is reapplied on every sign-in, so an instance cannot be
taken over by editing a row.
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import DbSession
from app.api.routes.auth import AdminUser
from app.models.account import Position, Session, User
from app.models.company import Company
from app.models.document import RawDocument
from app.models.factor import FactorScore
from app.models.signal import Signal
from app.models.usage import LLMUsageRun

logger = logging.getLogger(__name__)
router = APIRouter(tags=["admin"], prefix="/admin")


class AccountOut(BaseModel):
    id: str
    username: str
    email: str
    is_admin: bool
    verified: bool
    created_at: datetime | None
    last_seen_at: datetime | None
    positions: int
    active_sessions: int


class UsageOut(BaseModel):
    day: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class EngineOut(BaseModel):
    # Coverage, which is the number that actually describes this instance.
    companies: int
    companies_with_documents: int
    companies_with_findings: int
    companies_scored: int
    documents: int
    findings: int

    # Where the model budget went. The one resource on a free tier that runs
    # out, and the reason a company goes unread.
    usage: list[UsageOut]
    total_cost_usd: float

    latest_factor_run: str | None
    newest_document: datetime | None


@router.get("/accounts", response_model=list[AccountOut])
def list_accounts(db: DbSession, _: User = AdminUser):
    users = list(db.execute(select(User).order_by(User.created_at)).scalars())
    if not users:
        return []

    ids = [u.id for u in users]
    positions = dict(db.execute(
        select(Position.user_id, func.count()).where(Position.user_id.in_(ids)).group_by(Position.user_id)
    ).all())
    # Only sessions that would still authenticate. Counting expired rows would
    # report somebody as signed in on four devices when they are on none.
    sessions = dict(db.execute(
        select(Session.user_id, func.count())
        .where(Session.user_id.in_(ids))
        .where(Session.expires_at > datetime.now(timezone.utc))
        .group_by(Session.user_id)
    ).all())

    return [
        AccountOut(
            id=str(u.id), username=u.username, email=u.email,
            is_admin=u.is_admin, verified=u.email_verified_at is not None,
            created_at=u.created_at, last_seen_at=u.last_seen_at,
            positions=positions.get(u.id, 0),
            active_sessions=sessions.get(u.id, 0),
        )
        for u in users
    ]


@router.post("/accounts/{username}/revoke-sessions")
def revoke_sessions(username: str, db: DbSession, admin: User = AdminUser):
    """Sign an account out everywhere.

    Separate from deleting it, because the reason to do this is usually a lost
    laptop rather than a person leaving.
    """
    user = db.execute(
        select(User).where(User.username_lower == username.strip().lower())
    ).scalars().first()
    if user is None:
        raise HTTPException(status_code=404, detail=f"No account named {username!r}.")

    removed = db.execute(
        select(func.count()).select_from(Session).where(Session.user_id == user.id)
    ).scalar() or 0
    db.query(Session).filter(Session.user_id == user.id).delete(synchronize_session=False)
    db.commit()
    logger.warning("Admin %s revoked %d session(s) for %s.", admin.username, removed, user.username)
    return {"username": user.username, "revoked": removed}


@router.delete("/accounts/{username}")
def delete_account(username: str, db: DbSession, admin: User = AdminUser):
    """Remove an account and everything it owns.

    An admin cannot delete themselves. Not paternalism: this is the only route
    that can remove the last admin, and an instance with none cannot be
    administered again without a redeploy.
    """
    user = db.execute(
        select(User).where(User.username_lower == username.strip().lower())
    ).scalars().first()
    if user is None:
        raise HTTPException(status_code=404, detail=f"No account named {username!r}.")
    if user.id == admin.id:
        raise HTTPException(
            status_code=400,
            detail="You cannot delete the account you are signed in with.",
        )

    db.delete(user)
    db.commit()
    logger.warning("Admin %s deleted account %s.", admin.username, username)
    return {"deleted": user.username}


@router.get("/engine", response_model=EngineOut)
def engine_state(db: DbSession, _: User = AdminUser):
    """What the engine has actually covered, and what it spent doing it."""
    def count(model) -> int:
        return db.execute(select(func.count()).select_from(model)).scalar() or 0

    def distinct_companies(model) -> int:
        return db.execute(
            select(func.count(func.distinct(model.company_id)))
        ).scalar() or 0

    since = datetime.now(timezone.utc) - timedelta(days=14)
    usage_rows = db.execute(
        select(
            func.date(LLMUsageRun.created_at).label("day"),
            func.coalesce(func.sum(LLMUsageRun.calls), 0).label("calls"),
            func.coalesce(func.sum(LLMUsageRun.input_tokens), 0),
            func.coalesce(func.sum(LLMUsageRun.output_tokens), 0),
            func.coalesce(func.sum(LLMUsageRun.cost_usd), 0.0),
        )
        .where(LLMUsageRun.created_at >= since)
        .group_by(func.date(LLMUsageRun.created_at))
        .order_by(func.date(LLMUsageRun.created_at).desc())
    ).all()

    latest_factor = db.execute(
        select(func.max(FactorScore.as_of_date))
    ).scalar()
    newest_document = db.execute(
        select(func.max(RawDocument.published_at))
    ).scalar()

    return EngineOut(
        companies=count(Company),
        companies_with_documents=distinct_companies(RawDocument),
        companies_with_findings=distinct_companies(Signal),
        companies_scored=distinct_companies(FactorScore),
        documents=count(RawDocument),
        findings=count(Signal),
        usage=[
            UsageOut(
                day=str(row[0]), calls=int(row[1]),
                input_tokens=int(row[2]), output_tokens=int(row[3]),
                cost_usd=round(float(row[4]), 4),
            )
            for row in usage_rows
        ],
        total_cost_usd=round(
            float(db.execute(select(func.coalesce(func.sum(LLMUsageRun.cost_usd), 0.0))).scalar() or 0.0),
            4,
        ),
        latest_factor_run=str(latest_factor) if latest_factor else None,
        newest_document=newest_document,
    )
