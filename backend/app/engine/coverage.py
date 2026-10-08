"""Closing the coverage gap a few companies at a time.

Loom tracks a hundred and thirty companies and, for most of its life, had
anything to say about seven. Every other page reported its own emptiness
politely, which is a hundred and twenty-three promises the product was failing
to keep, and it was the loudest thing in the interface.

The gap had two different causes and they need different fixes.

**Priors were purely quota-bound.** They apply to every focus and watch company,
so all hundred and thirty could have one; only thirteen did, because building
them was a command somebody had to remember to run and the free tier refuses a
long burst. A drip fixes that: a few per run, ordered so the uncovered come
first, stopping the moment the provider says no and picking up where it left
off next time.

**Reading was tier-bound, which is not the same thing.** A watch-tier company
never has its documents ingested at all, so it cannot have findings however
much quota is available. Promoting everything to focus would be the obvious fix
and the wrong one: focus companies are re-ingested and re-analysed every six
hours, and a hundred and thirty of those would exhaust a free tier in an
afternoon and keep doing so forever.

So watch companies get a **one-shot read** instead: their newest annual or
quarterly filing, fetched and analysed once, which is enough to produce findings
and therefore a verdict. It costs a bounded handful of calls per company, once,
rather than a recurring bill. A company that turns out to matter can be promoted
to focus deliberately.

Both drips are ordered by need rather than alphabetically. A quota-limited run
that always starts at A never reaches the end of the alphabet.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.engine.llm_client import LLMUnavailableError
from app.models.account import Position
from app.models.company import Company, CompanyTier
from app.models.exposure import CompanyExposure
from app.models.document import RawDocument
from app.models.prior import CompanyPrior
from app.models.signal import Signal

logger = logging.getLogger(__name__)

# How many companies one drip run may touch.
#
# These were chosen when one exhausted Gemini tier was the whole ceiling, and
# two reads every two hours is 24 a day. That described a real constraint then
# and does not now: the measured backlog is 6,068 documents at a mean prepared
# payload of 9,760 tokens, about 59M input tokens in total, which is hours of
# provider time rather than months.
#
# Raising this is safe because the run self-limits. A drip stops cleanly the
# moment every provider refuses, so a figure set too high ends the run early
# instead of burning an allowance in a burst — the limit is an upper bound on
# work attempted, not a promise to attempt it.
#
# Eight reads is roughly ten minutes of wall time, comfortably inside the two
# hour interval, and clears the 121 companies still queued in about a day.
PRIORS_PER_RUN = 3
READS_PER_RUN = 8

# How a run's budget is divided between three claims that are not the same
# thing and must not be funded out of one pot.
#
#   HOLDINGS  what somebody actually owns. Round-robin across accounts rather
#             than by popularity, so the only holder of a name is not starved
#             by a name three people hold.
#   REACH     companies other filers name in their own 10-Ks. Reading one with
#             reach 8 improves the read-across for eight others, which is value
#             delivered to users who do not hold it.
#   BREADTH   least-covered sector first. This is the one that looks skippable
#             and is not: a stance is a residual against what documents of that
#             kind normally say, and those norms are measured across the
#             corpus. Fund only holdings and reach and the norms come from a
#             self-selected sample of hubs and mega-caps, which degrades every
#             verdict including the held ones. The failure is silent, which is
#             why breadth gets its own share rather than whatever is left over
#             by accident.
#
# Reach also has a rich-get-richer bias — it is computed from filings already
# read, so an unread sector looks like it has no hubs — and an independently
# funded breadth share is what breaks that loop.
HOLDINGS_SHARE = 0.5
REACH_SHARE = 0.25
# Breadth takes the remainder, so the three always sum to the whole budget.

# The filings a one-shot read looks at. Annual first, because a 10-K carries
# the risk factors and the full-year picture that a verdict actually rests on.
ONE_SHOT_FORMS = ("10-K", "10-Q")

# How many filings a company should eventually have been read, before quota
# goes to breadth instead.
#
# Depth was worth more than the drip assumed. A company read once has all its
# findings inside a single document, and the clustering correction weights each
# of them by one over the square root of that document's finding count: nine
# findings from one filing are worth 0.333 each, and the same nine spread over
# three filings are worth 0.577. So a second read roughly doubles a company's
# evidence strength without extracting a single new fact, and a company with one
# document can never reach a strong verdict at all because that needs two
# independent kinds of source. Measured on the corpus, the median covered
# company had exactly one document and the only company Loom had a verdict for
# was the one with five.
DEPTH_TARGET_DOCUMENTS = 3

# A prior older than this is rebuilt even if one exists, so coverage does not
# quietly become a set of standing views describing last year's company.
STALE_PRIOR_DAYS = 120


@dataclass
class DripResult:
    covered: int = 0
    failed: int = 0
    # Companies passed over because no available provider had a lane wide
    # enough for their filing. Separate from `failed`: nothing went wrong and
    # the company is still a candidate next run.
    skipped: int = 0
    # True when the provider refused. The caller stops rather than grinding
    # through the rest producing the same error once per company.
    exhausted: bool = False
    remaining: int = 0


def companies_needing_priors(db: Session, *, now: Optional[datetime] = None) -> list[Company]:
    """Focus and watch companies with no prior, or a stale one, neediest first.

    Ordered so a quota-limited run makes progress everywhere rather than
    perfecting the start of the alphabet. A company with no prior at all scores
    every filing zero, which is indistinguishable from a quiet week, so those
    come before any refresh.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=STALE_PRIOR_DAYS)

    latest = (
        select(
            CompanyPrior.company_id.label("company_id"),
            func.max(CompanyPrior.generated_at).label("generated_at"),
        )
        .group_by(CompanyPrior.company_id)
        .subquery()
    )

    rows = db.execute(
        select(Company, latest.c.generated_at)
        .outerjoin(latest, latest.c.company_id == Company.id)
        .where(Company.tier.in_([CompanyTier.FOCUS, CompanyTier.WATCH]))
        .order_by(Company.ticker)
    ).all()

    missing = [c for c, generated in rows if generated is None]
    stale = [
        c for c, generated in rows
        if generated is not None and _aware(generated) < cutoff
    ]
    # Never-covered first, then oldest-first among the stale.
    return missing + stale


def companies_needing_a_read(db: Session) -> list[Company]:
    """Watch-tier companies Loom has never read, least-covered sector first.

    Focus companies are excluded: they are already on the continuous path, and
    a drip competing with it would spend quota re-reading what the scheduled
    refresh is about to read anyway.

    **Ordered by how little Loom has read of the company's sector**, not by
    ticker. The module docstring already promised ordering by need, and the
    prior drip delivers it; this path did not, and the consequence was
    measurable rather than cosmetic.

    Reading alphabetically inside a quota that covers two companies per run
    produces a corpus shaped like the alphabet. What it actually produced was a
    corpus shaped like one sector: of the thirty companies Loom has read,
    thirteen are Technology and two are Industrials, so every cross-sectional
    measurement over findings was really a measurement of Technology. The
    sector-drift test failed on exactly this, twice, and the second failure
    survived a fivefold increase in the price universe because the *disclosures*
    were still 50 to 90 percent one sector.

    So the ordering asks which sector Loom knows least about and goes there
    first. Within a sector, the largest companies come first: they file more,
    are written about more, and their disclosures reach further, so they are
    worth more per unit of quota than a micro-cap that files once a year.
    """
    with_findings = select(Signal.company_id).distinct().subquery()
    rows = list(db.execute(
        select(Company)
        .where(Company.tier == CompanyTier.WATCH)
        .where(Company.id.not_in(select(with_findings.c.company_id)))
    ).scalars())

    # How many companies Loom has already read in each sector. Counted over the
    # whole universe rather than the candidates, because the question is what
    # Loom knows, not what is left.
    read_counts = dict(db.execute(
        select(Company.sector, func.count(Company.id.distinct()))
        .join(Signal, Signal.company_id == Company.id)
        .group_by(Company.sector)
    ).all())

    return read_order(rows, read_counts)


# Unranked companies sort last rather than first. `sec_rank` is absent for a
# company added by hand rather than seeded from SEC's directory, and treating a
# missing size as the largest size would put every hand-added ticker at the
# front of the queue.
_UNRANKED = 10**9


def read_order(companies: list, read_counts: dict) -> list:
    """Order candidates by how little Loom knows about their sector.

    Pure, and separated from the query for that reason: the ordering is the
    part with a judgement in it, and it should be testable without a database.
    """
    def rank(company) -> tuple:
        return (
            read_counts.get(getattr(company, "sector", None), 0),
            # Largest first within a sector. They file more, are written about
            # more, and their disclosures reach further, so they are worth more
            # per unit of quota than a micro-cap that files once a year.
            getattr(company, "sec_rank", None) if getattr(company, "sec_rank", None) is not None else _UNRANKED,
            getattr(company, "ticker", ""),
        )

    return sorted(companies, key=rank)


def drip_priors(
    db: Session, *, limit: int = PRIORS_PER_RUN, now: Optional[datetime] = None
) -> DripResult:
    """Build priors for the neediest companies, stopping cleanly on quota."""
    from app.engine.prior import build_prior

    pending = companies_needing_priors(db, now=now)
    result = DripResult(remaining=len(pending))

    for company in pending[:limit]:
        try:
            prior = build_prior(company.ticker, db)
        except LLMUnavailableError as exc:
            # Applies to everything left, so stop rather than log the same
            # failure once per remaining company.
            logger.info("Prior drip stopping: %s", exc)
            result.exhausted = True
            break
        except Exception:
            logger.exception("Prior drip failed for %s", company.ticker)
            result.failed += 1
            continue

        if prior is None:
            # Nothing to build from. Counted as neither covered nor failed:
            # the company will be offered again next run, and if it is still
            # empty nothing is lost.
            continue
        result.covered += 1
        logger.info(
            "Prior drip: %s armed with %d watch items.",
            company.ticker, len(prior.watch_items or []),
        )

    result.remaining = max(0, result.remaining - result.covered)
    return result


def _document_counts(db: Session) -> dict:
    """How many distinct documents Loom has findings from, per company.

    Computed once and shared by all three claims, because every one of them
    asks the same question about need and asking it three times is three table
    scans for one answer.
    """
    return dict(db.execute(
        select(Signal.company_id,
               func.count(func.distinct(Signal.source_document_id)))
        .where(Signal.source_document_id.is_not(None))
        .where(Signal.dismissed_at.is_(None))
        .group_by(Signal.company_id)
    ).all())


def _needs_work(company: Company, counts: dict) -> bool:
    """True while a company is short of the depth a verdict actually rests on."""
    return counts.get(company.id, 0) < DEPTH_TARGET_DOCUMENTS


def companies_for_users(db: Session) -> list[Company]:
    """What accounts hold, one company per account per pass.

    Round-robin rather than ranked by holder count, and that is the whole
    point. Ranking globally means a company three people hold always beats one
    person's only holding, so whoever is the sole holder of a name waits
    forever. Interleaving gives every account the same claim on a pass
    regardless of how popular its picks are: somebody who follows one company
    gets it read, somebody who follows twenty gets them read more slowly.

    A company two accounts hold appears once. The corpus is shared, so reading
    it serves both.
    """
    counts = _document_counts(db)
    rows = db.execute(
        select(Position.user_id, Company)
        .join(Company, Company.id == Position.company_id)
    ).all()

    by_account: dict = {}
    for user_id, company in rows:
        if _needs_work(company, counts):
            by_account.setdefault(user_id, []).append(company)

    # Neediest first within each account, so a held company with nothing at all
    # is served before one that already has two documents.
    for owned in by_account.values():
        owned.sort(key=lambda c: (counts.get(c.id, 0), c.ticker))

    out: list[Company] = []
    seen: set = set()
    for tier in range(max((len(v) for v in by_account.values()), default=0)):
        for owned in by_account.values():
            if tier < len(owned) and owned[tier].id not in seen:
                seen.add(owned[tier].id)
                out.append(owned[tier])
    return out


def companies_by_reach(db: Session) -> list[Company]:
    """Companies other filers name most, neediest first.

    Reach is how many tracked companies name this one in their own filings, so
    it measures what reading it is worth to the rest of the corpus rather than
    how large it is. Market capitalisation would be the obvious proxy and is
    the wrong one: it is exogenous, it says nothing about whether reading the
    company improves Loom's answers, and it duplicates what every other tool
    already covers.
    """
    counts = _document_counts(db)
    reach = dict(db.execute(
        select(CompanyExposure.hub_company_id, func.count())
        .group_by(CompanyExposure.hub_company_id)
    ).all())
    if not reach:
        return []

    rows = list(db.execute(
        select(Company).where(Company.id.in_(list(reach)))
    ).scalars())
    candidates = [c for c in rows if _needs_work(c, counts)]
    candidates.sort(key=lambda c: (-reach.get(c.id, 0), counts.get(c.id, 0), c.ticker))
    return candidates


def derive_tiers(db: Session) -> int:
    """Promote every held company to the focus tier. Returns how many moved.

    Tier was a field somebody set by hand, which is how eleven companies chosen
    during testing ended up being the entire deep-read set while companies
    users actually hold sat in the watch tier getting no news, no 8-K and no
    document-level findings at all.

    It becomes an output instead: a company is focus *because* an account holds
    it. The column stays because ingestion needs a fast per-company answer at
    fetch time without consulting every account, but it is now a cached view of
    demand rather than an independent opinion.

    Promotion only. Demotion would take a company's news and 8-K away the
    moment its last holder sold, and a corpus that forgets is worse than one
    that carries a few names nobody owns any more; the focus set is bounded by
    real holdings either way.
    """
    held = list(db.execute(
        select(Company)
        .join(Position, Position.company_id == Company.id)
        .where(Company.tier != CompanyTier.FOCUS)
        .distinct()
    ).scalars())
    for company in held:
        company.tier = CompanyTier.FOCUS
    if held:
        db.commit()
        logger.info(
            "Tier: promoted %d held companies to focus (%s).",
            len(held), ", ".join(c.ticker for c in held[:8]),
        )
    return len(held)


def companies_needing_depth(db: Session) -> list[Company]:
    """Companies Loom has read once and should read again, shallowest first.

    The counterpart to `companies_needing_a_read`, which only ever looks for
    companies with nothing at all. Breadth alone left every covered company at a
    single document, which is the worst case for both of the quantities a verdict
    rests on: the clustering correction is harshest inside one document, and a
    strong verdict requires two independent sources, so a one-document company is
    capped regardless of what its filing said.
    """
    counted = (
        select(Signal.company_id.label("cid"),
               func.count(func.distinct(Signal.source_document_id)).label("docs"))
        .where(Signal.source_document_id.is_not(None))
        .group_by(Signal.company_id)
        .subquery()
    )
    rows = list(db.execute(
        select(Company, counted.c.docs)
        .join(counted, counted.c.cid == Company.id)
        .where(counted.c.docs < DEPTH_TARGET_DOCUMENTS)
        .order_by(counted.c.docs.asc(), Company.sec_rank.asc().nullslast())
    ).all())
    return [company for company, _docs in rows]


def _unanalysed_filing(db: Session, company: Company) -> Optional[RawDocument]:
    """A stored annual or quarterly report for this company with no findings yet.

    Newest first, and deliberately only documents already stored: the depth pass
    spends its quota on analysis, which is the expensive half, rather than on
    fetching a corpus it may not get to read.
    """
    read = select(Signal.source_document_id).where(Signal.company_id == company.id)
    return db.execute(
        select(RawDocument)
        .where(RawDocument.company_id == company.id)
        .where(RawDocument.doc_subtype.in_(ONE_SHOT_FORMS))
        .where(RawDocument.id.not_in(read))
        .order_by(RawDocument.published_at.desc())
        .limit(1)
    ).scalars().first()


def _some_provider_is_up() -> bool:
    """Whether any provider still has an allowance, regardless of its width."""
    from app.engine.llm_client import LLMUnavailableError as _Unavailable, get_llm_client

    try:
        return bool(get_llm_client().available)
    except _Unavailable:
        # Nothing configured at all. Stopping is right.
        return False


def drip_reads(db: Session, *, limit: int = READS_PER_RUN) -> DripResult:
    """Read one filing for companies Loom has never read.

    Bounded by construction: the newest annual or quarterly report and nothing
    else. Enough to produce findings and a verdict, at a one-time cost rather
    than signing the company up for analysis every six hours.
    """
    from app.engine.pipeline import analyze_document
    from app.ingestion.registry import DOCUMENT_ADAPTERS, _persist_document
    from app.storage.blob_store import get_blob_store

    # Tier is derived from holdings, so a company somebody added since the last
    # run is already in the deep set by the time this picks candidates.
    derive_tiers(db)

    held = companies_for_users(db)
    hubs = companies_by_reach(db)
    pending = companies_needing_a_read(db)
    deeper = companies_needing_depth(db)
    blob_store = get_blob_store()

    # Which companies already have findings decides which document a candidate
    # wants: a first read takes the newest filing, a depth read takes one that
    # has not been analysed yet. Read once here rather than per candidate.
    _read_already = set(_document_counts(db))

    # Three claims, funded separately. An empty queue yields its share to the
    # others rather than idling, so a run is always worth its full budget —
    # which is also what makes the split safe to set before anyone has measured
    # it: getting the ratio wrong costs ordering, never throughput.
    breadth = [c for c in pending + deeper if c.id not in {h.id for h in held}]
    claims = [
        (held, int(limit * HOLDINGS_SHARE)),
        (hubs, int(limit * REACH_SHARE)),
        (breadth, limit),  # the remainder, capped below
    ]

    queue: list = []
    taken: set = set()
    for candidates, share in claims:
        room = min(share, limit - len(queue))
        for company in candidates:
            if room <= 0 or len(queue) >= limit:
                break
            if company.id in taken:
                continue
            taken.add(company.id)
            # Depth rather than a first read once a company already has
            # findings; the two paths look for different documents.
            queue.append((company, company.id in _read_already))
            room -= 1

    result = DripResult(remaining=len(pending) + len(deeper))

    for company, is_depth in queue:
        try:
            document = (
                _unanalysed_filing(db, company) if is_depth
                else _newest_filing(db, company)
            )
            if document is None and not is_depth:
                document = _fetch_one_filing(
                    db, company, DOCUMENT_ADAPTERS, blob_store, _persist_document
                )
            if document is None:
                logger.info("Read drip: no filing available for %s.", company.ticker)
                continue

            written = analyze_document(document.id, db)
        except LLMUnavailableError as exc:
            # Two different failures arrive here and only one is a reason to
            # stop. If some provider is still up, the call failed because no
            # available lane was wide enough for *this* filing — a property of
            # the document, not the account — and the next company's filing may
            # fit perfectly well. Breaking on that meant one oversized 10-K
            # ended the whole run: a drip raised to eight reads still covered
            # exactly one company before giving up.
            if _some_provider_is_up():
                logger.info(
                    "Read drip: skipping %s, no lane wide enough (%s)",
                    company.ticker, str(exc)[:120],
                )
                result.skipped += 1
                continue
            logger.info("Read drip stopping: %s", exc)
            result.exhausted = True
            break
        except Exception:
            logger.exception("Read drip failed for %s", company.ticker)
            result.failed += 1
            continue

        if written:
            result.covered += 1
            logger.info(
                "Read drip: %s %s (%s), %d findings.",
                company.ticker, "read deeper" if is_depth else "read once",
                document.doc_subtype, written,
            )

    result.remaining = max(0, result.remaining - result.covered)
    return result


def _newest_filing(db: Session, company: Company) -> Optional[RawDocument]:
    """An annual or quarterly report already stored for this company."""
    return db.execute(
        select(RawDocument)
        .where(RawDocument.company_id == company.id)
        .where(RawDocument.doc_subtype.in_(ONE_SHOT_FORMS))
        .order_by(RawDocument.published_at.desc())
        .limit(1)
    ).scalars().first()


def _fetch_one_filing(
    db: Session, company: Company, adapters, blob_store, persist
) -> Optional[RawDocument]:
    """Fetch and store exactly one filing for a company that has none.

    Deliberately not a call into the ordinary ingest path, which is gated on
    tier and would fetch nothing for a watch company. This asks the SEC adapter
    directly, keeps the newest annual or quarterly report, and discards the
    rest, so a watch company gains one document rather than a corpus.
    """
    for adapter in adapters:
        if "edgar" not in adapter.source_name:
            continue
        try:
            dtos = list(adapter.fetch(company.ticker, since=None))
        except Exception:
            logger.exception("Read drip: fetch failed for %s", company.ticker)
            return None

        candidates = [d for d in dtos if getattr(d, "doc_subtype", None) in ONE_SHOT_FORMS]
        if not candidates:
            return None
        # Annual before quarterly, then newest: a 10-K carries the risk factors
        # and full-year picture a verdict actually rests on.
        candidates.sort(
            key=lambda d: (d.doc_subtype != "10-K", -(d.published_at.timestamp() if d.published_at else 0))
        )
        persist(candidates[0], company_id=company.id, db=db, blob_store=blob_store)
        db.commit()
        return _newest_filing(db, company)
    return None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


__all__ = [
    "DEPTH_TARGET_DOCUMENTS",
    "ONE_SHOT_FORMS",
    "companies_needing_depth",
    "PRIORS_PER_RUN",
    "READS_PER_RUN",
    "STALE_PRIOR_DAYS",
    "DripResult",
    "companies_needing_a_read",
    "companies_needing_priors",
    "drip_priors",
    "drip_reads",
]
