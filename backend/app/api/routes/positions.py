"""What a user owns or is watching, and what Loom makes of it.

The table this serves is the one that changes what Loom is. Every other view
describes a company; this one lets Loom say "the case for something you hold
has weakened", which is the only sentence in the product worth interrupting
somebody for.

A watch and a holding are the same object with and without a size, because the
difference really is one number and splitting them into two lists means two
things to keep in step.
"""

from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DbSession
from app.api.routes.auth import CurrentUser
from app.ingestion.prices import get_price_source
from app.models.account import Position, User
from app.models.brief import CompanyBrief
from app.models.company import Company
from app.models.factor import COMPOSITE_KEY, FactorScore

router = APIRouter(tags=["positions"])


class PositionIn(BaseModel):
    ticker: str = Field(max_length=12)
    # Both optional: adding a company to watch is the same action as adding a
    # holding, minus the numbers.
    shares: float | None = Field(default=None, ge=0)
    cost_basis: float | None = Field(default=None, ge=0)
    note: str | None = Field(default=None, max_length=2000)
    opened_at: datetime | None = None


class PositionOut(BaseModel):
    ticker: str
    name: str
    shares: float | None
    cost_basis: float | None
    note: str | None
    opened_at: datetime | None
    # True once it has a size. Held positions are what Loom prioritises.
    is_held: bool

    # Live, so a holdings table is worth looking at during a session. Null
    # when the provider is unreachable, never zero: a zero would read as a
    # wipeout rather than as a missing quote.
    last_price: float | None = None
    market_value: float | None = None
    unrealised: float | None = None
    unrealised_percent: float | None = None

    # What Loom thinks, folded in so a portfolio row carries a judgement rather
    # than only a number.
    stance: str | None = None
    headline: str | None = None
    numbers_percentile: float | None = None


class DigestSettingIn(BaseModel):
    frequency: str


class DigestSettingOut(BaseModel):
    frequency: str
    verified: bool
    message: str


class PortfolioOut(BaseModel):
    positions: list[PositionOut]
    held_count: int
    watching_count: int
    total_value: float | None
    total_cost: float | None
    total_unrealised: float | None


def _price(ticker: str) -> float | None:
    try:
        series = get_price_source().get(ticker, "24H")
    except Exception:
        return None
    return series.last if series else None


def _to_out(position: Position, company: Company, brief, composite) -> PositionOut:
    shares = float(position.shares) if position.shares is not None else None
    cost = float(position.cost_basis) if position.cost_basis is not None else None
    price = _price(company.ticker)

    value = shares * price if (shares is not None and price is not None) else None
    spent = shares * cost if (shares is not None and cost is not None) else None
    unrealised = value - spent if (value is not None and spent is not None) else None
    percent = (
        (unrealised / spent * 100) if (unrealised is not None and spent) else None
    )

    return PositionOut(
        ticker=company.ticker,
        name=company.name,
        shares=shares,
        cost_basis=cost,
        note=position.note,
        opened_at=position.opened_at,
        is_held=position.is_held,
        last_price=price,
        market_value=value,
        unrealised=unrealised,
        unrealised_percent=round(percent, 2) if percent is not None else None,
        stance=brief.stance.value if brief else None,
        headline=brief.headline if brief else None,
        numbers_percentile=composite,
    )


def _context(db, company_ids: list):
    """Latest brief and composite percentile for a set of companies, in two
    queries rather than two per row."""
    briefs: dict = {}
    for brief in db.execute(
        select(CompanyBrief)
        .where(CompanyBrief.company_id.in_(company_ids))
        .order_by(CompanyBrief.company_id, CompanyBrief.generated_at)
    ).scalars():
        briefs[brief.company_id] = brief

    composites: dict = {}
    latest_date = db.execute(
        select(FactorScore.as_of_date).order_by(FactorScore.as_of_date.desc()).limit(1)
    ).scalars().first()
    if latest_date is not None:
        for score in db.execute(
            select(FactorScore)
            .where(FactorScore.company_id.in_(company_ids))
            .where(FactorScore.as_of_date == latest_date)
            .where(FactorScore.factor_key == COMPOSITE_KEY)
        ).scalars():
            composites[score.company_id] = score.percentile
    return briefs, composites


@router.get("/positions", response_model=PortfolioOut)
def list_positions(db: DbSession, user: User = CurrentUser):
    rows = db.execute(
        select(Position, Company)
        .join(Company, Company.id == Position.company_id)
        .where(Position.user_id == user.id)
        .order_by(Company.ticker)
    ).all()
    if not rows:
        return PortfolioOut(
            positions=[], held_count=0, watching_count=0,
            total_value=None, total_cost=None, total_unrealised=None,
        )

    briefs, composites = _context(db, [c.id for _, c in rows])
    out = [
        _to_out(p, c, briefs.get(c.id), composites.get(c.id))
        for p, c in rows
    ]
    # Holdings first, then by size. A watch item is not competing for attention
    # with money at risk.
    out.sort(key=lambda p: (p.is_held, p.market_value or 0.0), reverse=True)

    held = [p for p in out if p.is_held]
    values = [p.market_value for p in held if p.market_value is not None]
    costs = [
        (p.shares or 0) * (p.cost_basis or 0)
        for p in held if p.shares is not None and p.cost_basis is not None
    ]
    return PortfolioOut(
        positions=out,
        held_count=len(held),
        watching_count=len(out) - len(held),
        total_value=round(sum(values), 2) if values else None,
        total_cost=round(sum(costs), 2) if costs else None,
        total_unrealised=(
            round(sum(values) - sum(costs), 2) if values and costs else None
        ),
    )


@router.put("/positions/{ticker}", response_model=PositionOut)
def upsert_position(ticker: str, payload: PositionIn, db: DbSession, user: User = CurrentUser):
    """Create or update in one call.

    Idempotent on purpose: the client does not have to know whether a company
    is already tracked before saving, which removes a round trip and a race.
    """
    symbol = ticker.upper().strip()
    company = db.execute(select(Company).where(Company.ticker == symbol)).scalars().first()
    if company is None:
        raise HTTPException(status_code=404, detail=f"Loom does not track {symbol}.")

    position = db.execute(
        select(Position)
        .where(Position.user_id == user.id)
        .where(Position.company_id == company.id)
    ).scalars().first()

    if position is None:
        position = Position(user_id=user.id, company_id=company.id)
        db.add(position)

    position.shares = Decimal(str(payload.shares)) if payload.shares is not None else None
    position.cost_basis = Decimal(str(payload.cost_basis)) if payload.cost_basis is not None else None
    position.note = payload.note
    position.opened_at = payload.opened_at or (
        position.opened_at or (datetime.now(timezone.utc) if payload.shares else None)
    )
    db.commit()
    db.refresh(position)

    briefs, composites = _context(db, [company.id])
    return _to_out(position, company, briefs.get(company.id), composites.get(company.id))


@router.delete("/positions/{ticker}")
def delete_position(ticker: str, db: DbSession, user: User = CurrentUser):
    symbol = ticker.upper().strip()
    company = db.execute(select(Company).where(Company.ticker == symbol)).scalars().first()
    if company is None:
        raise HTTPException(status_code=404, detail=f"Loom does not track {symbol}.")

    position = db.execute(
        select(Position)
        .where(Position.user_id == user.id)
        .where(Position.company_id == company.id)
    ).scalars().first()
    if position is not None:
        db.delete(position)
        db.commit()
    return {"ok": True}


@router.get("/digest", response_model=DigestSettingOut)
def digest_setting(db: DbSession, user: User = CurrentUser):
    return _digest_out(user)


@router.put("/digest", response_model=DigestSettingOut)
def set_digest(payload: DigestSettingIn, db: DbSession, user: User = CurrentUser):
    """How often Loom may interrupt this person.

    Turning it off is one call and takes effect immediately, with no
    confirmation step and no "are you sure". A tool that makes leaving harder
    than arriving does not deserve the inbox.
    """
    from app.services.digest import FREQUENCIES

    if payload.frequency not in FREQUENCIES:
        raise HTTPException(
            status_code=400,
            detail=f"Choose one of: {', '.join(FREQUENCIES)}.",
        )
    user.digest_frequency = payload.frequency
    db.commit()
    return _digest_out(user)


def _digest_out(user: User) -> DigestSettingOut:
    if user.digest_frequency == "off":
        message = "Loom will not email you."
    elif user.email_verified_at is None:
        # Said rather than silently not sending. Somebody who set a preference
        # and receives nothing deserves to know it was the address, not a bug.
        message = "Confirm your email address before Loom can send anything."
    else:
        message = (
            f"Loom emails {user.email} when something moves in your companies, at "
            f"most once a {'day' if user.digest_frequency == 'daily' else 'week'}. "
            "Nothing is sent on a quiet day."
        )
    return DigestSettingOut(
        frequency=user.digest_frequency,
        verified=user.email_verified_at is not None,
        message=message,
    )
