"""The dashboard's running feed. Thin route over engine/tape.py."""

from fastapi import APIRouter

from app.api.deps import DbSession
from app.engine.tape import build_tape
from app.schemas.tape import TapeItemOut

router = APIRouter(tags=["tape"])


@router.get("/tape", response_model=list[TapeItemOut])
def tape(db: DbSession):
    """Upcoming reports and recent headlines for whatever is on the watchlist.

    Derived from the watchlist on every call rather than from a fixed list, so
    a company added a minute ago is on the tape without anything being
    redeployed, and a company removed from it disappears.
    """
    return build_tape(db)
