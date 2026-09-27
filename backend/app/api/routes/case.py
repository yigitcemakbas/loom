"""One company, as one argument.

Assembles what every other endpoint serves separately and hands it to
engine/case.py to rank. The route does the loading; the engine does the
judgement, and holds no session, so the ordering can be tested without a
database.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import CompanyRepo, DbSession
from app.api.routes.auth import OptionalUser
from app.engine.case import build_case
from app.engine.price_context import moves_for, sector_move_after, standing as price_standing
from app.engine.disclosure import UNINFORMATIVE_FLOOR
from app.engine.norms import load_norms, load_precedents
from app.engine.precedent import primary_topic_of_signal
from app.engine.price_loader import load_benchmark, load_history, load_peers
from app.engine.contradiction import find_contradictions
from app.engine.quant.crosssection import percentile_phrase
from app.engine.quant.factors import FACTORS_BY_KEY
from app.models.account import Position, User
from app.models.company import Company
from app.models.event_assessment import EventAssessment
from app.models.factor import COMPOSITE_KEY, FactorScore
from app.repositories.brief_repository import BriefRepository
from app.repositories.prior_repository import PriorRepository
from app.repositories.signal_repository import SignalRepository

router = APIRouter(tags=["case"])

# How many findings feed the case. The ranking decides what a reader sees, so
# the cap is about assembly cost rather than about presentation: beyond this,
# older findings cannot outrank what is already there.
MAX_FINDINGS = 60

# Matched filings older than this cannot be news, and the engine drops them
# anyway. Bounded here so the query does not read a year of history to discard
# most of it.
EVENT_WINDOW_DAYS = 30


class CasePointOut(BaseModel):
    key: str
    headline: str
    detail: str
    side: str
    weight: int
    source: str
    settles_it: str | None = None
    quote: str | None = None
    # The disclosure its kind of document always contains, which carried no
    # weight in the verdict. Marked so a long list of concerns below the cut
    # does not read as evidence the verdict ignored.
    routine: bool = False


class CaseBasisOut(BaseModel):
    """What the case rests on, so a reader knows before they weigh any of it."""

    findings: int
    factors: int
    peers: int
    sector: str | None
    has_valuation: bool
    has_price: bool
    is_read: bool
    summary: str


class CaseFileOut(BaseModel):
    ticker: str
    name: str
    stance: str | None
    headline: str
    confidence: float
    points: list[CasePointOut]
    # What the price has already done. A separate block from `points` because
    # it is not an argument for or against anything: it is the condition the
    # arguments are read in.
    price: list[CasePointOut]
    # Everything that ranked below the cap, in order. Served rather than
    # summarised as a count, so the interface can open it on request instead of
    # telling a reader that evidence exists somewhere else.
    withheld: list[CasePointOut]
    valuation: list[CasePointOut]
    gaps: list[str]
    held: bool
    # Surfaced separately from `points` so the interface cannot bury it. A page
    # showing only the case for a conclusion is a sales pitch.
    strongest_against: CasePointOut | None = None
    basis: CaseBasisOut | None = None


class _FactorView:
    """The shape the case engine expects, built from a stored score.

    A small adapter rather than passing ORM rows in, so the engine keeps its
    promise not to know about the database.
    """

    def __init__(self, score: FactorScore):
        factor = FACTORS_BY_KEY.get(score.factor_key)
        self.key = score.factor_key
        self.percentile = score.percentile
        self.label = factor.label if factor else score.factor_key.replace("_", " ")
        self.meaning = factor.meaning if factor else ""
        self.source = factor.source if factor else "reported financial statements"
        self.phrase = percentile_phrase(score.percentile)


@router.get("/companies/{ticker}/case", response_model=CaseFileOut)
def company_case(
    ticker: str,
    company_repo: CompanyRepo,
    db: DbSession,
    user: User | None = OptionalUser,
):
    company = company_repo.get_by_ticker(ticker.upper())
    if company is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker {ticker!r}")

    findings = SignalRepository(db).list_feed(company_id=company.id, limit=MAX_FINDINGS)
    brief = BriefRepository(db).latest_for(company.id)
    prior = PriorRepository(db).latest_for(company.id)

    as_of = db.execute(select(func.max(FactorScore.as_of_date))).scalar()
    scores = []
    percentiles: dict[str, float] = {}
    if as_of is not None:
        scores = list(db.execute(
            select(FactorScore)
            .where(FactorScore.company_id == company.id)
            .where(FactorScore.as_of_date == as_of)
        ).scalars())
        percentiles = {
            s.factor_key: s.percentile for s in scores if s.percentile is not None
        }

    events = list(db.execute(
        select(EventAssessment)
        .where(EventAssessment.company_id == company.id)
        .where(EventAssessment.occurred_at >= datetime.now(timezone.utc) - timedelta(days=EVENT_WINDOW_DAYS))
        .where(EventAssessment.score >= 1.0)
        .order_by(EventAssessment.score.desc())
        .limit(5)
    ).scalars())

    # What the market has already done with the same disclosures. Loaded here
    # rather than inside the engine so the ranking stays a pure function of
    # already-computed data and can be tested without a database.
    history = load_history(db, company.id)
    benchmark = load_benchmark(db)
    standing = price_standing(history, datetime.now(timezone.utc).date())
    moves = moves_for(findings, history, benchmark)

    # What has followed comparable disclosures at *other* companies. The
    # company's own history is excluded: a precedent built partly from this
    # company, shown against this company's finding, is the company predicting
    # itself, and at these sample sizes one firm with six filings can be half
    # the evidence.
    # Which findings said nothing their kind of document does not always say.
    # The same judgement the stance already made, surfaced so a reader can see
    # why a long list of concerns did not move the verdict.
    norms = load_norms(db)
    routine_ids = {
        str(f.id) for f in findings
        if (excess := norms.excess_for(f)) is not None
        and abs(excess) < UNINFORMATIVE_FLOOR
    }

    base = load_precedents(db)
    precedents = {}
    for finding in findings:
        # The topic the finding is most about, not an arbitrary one of the
        # several it may match. A finding headed "Trade disputes and
        # international conflict" was being calibrated against the
        # supply-concentration record because set iteration order chose it.
        topic = primary_topic_of_signal(finding)
        if topic is None:
            continue
        found = base.lookup(topic, company.sector, exclude_ticker=company.ticker)
        if found is not None:
            precedents[str(finding.id)] = found

    # The move the market made after the most recent filing Loom read, which is
    # the one a disagreement with the tape is about.
    #
    # The most recent *measurable* move, not the most recent finding's. A
    # fortnight has to elapse before a fortnight's reaction exists, so keying
    # this on the newest finding returned None for every company under active
    # coverage, which is precisely the set the comparison is worth making for.
    # The most recent move large enough to count as a reaction at all, rather
    # than simply the most recent move. Those differ: a procedural announcement
    # filed after an annual report has its own quiet fortnight, and taking it
    # would report "the market did nothing" while hiding the repricing of the
    # document the reading actually rests on.
    #
    # Selecting on size is not selecting on the answer. Whether a move is
    # material is decided against this company's own volatility, before
    # anything looks at which way it went or what Loom concluded.
    reactions = [m for m in moves.values() if m.is_material]
    latest_move = max(reactions, key=lambda m: m.as_of) if reactions else None

    # And whether that move belonged to this company or to its whole industry.
    # Measured only where there was a move to attribute: asking who owns a
    # reaction that did not happen produces a sentence about nothing.
    sector_move = None
    if latest_move is not None and company.sector:
        sector_move = sector_move_after(
            history, load_peers(db, company), benchmark, latest_move.as_of, company.sector,
        )

    contradictions = find_contradictions(
        findings, percentiles, stance=brief.stance.value if brief else None,
        move=latest_move,
    )

    # How many companies the percentiles were computed against. A ranking is a
    # statement about a peer group, and its size is the first thing that
    # decides whether the statement is worth anything.
    peers = 0
    if company.sector:
        peers = db.execute(
            select(func.count(Company.id)).where(Company.sector == company.sector)
        ).scalar() or 0

    held = False
    if user is not None:
        held = db.execute(
            select(Position.id)
            .where(Position.user_id == user.id)
            .where(Position.company_id == company.id)
        ).scalars().first() is not None

    case = build_case(
        ticker=company.ticker,
        name=company.name,
        brief=brief,
        contradictions=contradictions,
        # The composite is excluded: it backtested at no better than chance
        # over 203 rebalances, and a case file is where a reader looks for
        # reasons rather than for every number Loom holds.
        factors=[_FactorView(s) for s in scores if s.factor_key != COMPOSITE_KEY],
        findings=findings,
        events=events,
        prior=prior,
        held=held,
        standing=standing,
        moves=moves,
        precedents=precedents,
        sector_move=sector_move,
        routine_ids=routine_ids,
        peers=peers,
        sector=company.sector,
    )

    strongest = case.strongest_against
    return CaseFileOut(
        ticker=case.ticker, name=case.name, stance=case.stance,
        headline=case.headline, confidence=case.confidence,
        points=[CasePointOut(**p.__dict__) for p in case.points],
        price=[CasePointOut(**p.__dict__) for p in case.price],
        withheld=[CasePointOut(**p.__dict__) for p in case.withheld],
        valuation=[CasePointOut(**p.__dict__) for p in case.valuation],
        gaps=case.gaps, held=case.held,
        strongest_against=CasePointOut(**strongest.__dict__) if strongest else None,
        basis=CaseBasisOut(
            **case.basis.__dict__,
            is_read=case.basis.is_read,
            summary=case.basis.summary,
        ) if case.basis else None,
    )
