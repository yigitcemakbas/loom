"""Orchestration only. Contains no analysis logic of its own.

Each step lives in its own module, sections, extraction, diffing,
signal_writer, priority, and this file decides what runs in what order and
handles bookkeeping. Keeping it free of judgement logic is what lets every
other engine module be tested in isolation.
"""

import logging
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.engine import brief as brief_engine
from app.engine import clustering, diffing, extraction, fact_rules, signal_writer
from app.engine.llm_client import PROMPT_VERSION, LLMClient, LLMUnavailableError, get_llm_client
from app.engine.prompts import emerging_pattern, news_digest, quarter_comparison, risk_diff
from app.engine.prompts.emerging_pattern import EmergingPatternResult
from app.engine.prompts.news_digest import NewsDigestResult
from app.engine.prompts.quarter_comparison import QuarterComparisonResult
from app.engine.prompts.risk_diff import RiskDiffResult
from app.models.signal import AnalysisStatus
from app.repositories.company_repository import CompanyRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.brief_repository import BriefRepository
from app.repositories.fact_repository import FactRepository
from app.repositories.signal_repository import SignalRepository
from app.repositories.usage_repository import UsageRepository
from app.storage.blob_store import get_blob_store

logger = logging.getLogger(__name__)

# "Recent" scope: what gets analysed automatically. Everything older is
# available on demand, so a first run costs a few dollars rather than a few
# hundred across ~873 stored documents.
RECENT_8K_DAYS = 90

# News (Phase 3) arrives at a completely different cadence from filings: a
# single active week can produce more items than a year of 8-Ks. Both a shorter
# window and a hard cap, because one busy news cycle must not be able to burn a
# day's free-tier quota on low-value items.
RECENT_NEWS_DAYS = 14
MAX_NEWS_DOCUMENTS = 15

# Transcripts are quarterly and long; the two most recent are what a reader
# would actually compare.
MAX_TRANSCRIPTS = 2

# History loaded for short-window synthesis. Wide enough to contain the most
# recent burst plus the baseline it is judged against (clustering anchors its
# window on the newest disclosure, not on today), and narrow enough that a
# pattern surfaced from it is still worth the word "emerging".
PATTERN_LOOKBACK_DAYS = 180

# History the threshold rules reason over. Wide enough to hold several
# short-interest readings and a quarter's insider activity.
FACT_LOOKBACK_DAYS = 180

# Which section of each periodic filing is worth diffing against the previous
# one of its kind. Before this, only 10-K risk factors were ever compared, so
# 214 stored quarterly reports were each analysed in isolation and the app
# could only ever say what changed year over year, which is the wrong cadence
# for a decision taken around a quarterly earnings event.
COMPARISON_PLAN = {
    # Risk factors: restated in full each year, so a genuine addition is
    # meaningful and detectable.
    "10-K": {"section": "1A", "comparison": "year_over_year_risk_factors", "kind": "risk"},
    # Management's discussion of the quarter. A 10-Q's own risk section is
    # normally a cross reference back to the annual report, whereas Item 2 is
    # where demand, margins, and outlook are actually discussed.
    "10-Q": {"section": "2", "comparison": "quarter_over_quarter_mdna", "kind": "quarter"},
}


def _document_text(blob_uri: str) -> str:
    return get_blob_store().get(blob_uri).decode("utf-8", errors="replace")


def analyze_document(
    document_id: uuid.UUID,
    db: Session,
    *,
    client: LLMClient | None = None,
    force: bool = False,
) -> int:
    """Analyse one document. Returns the number of signals written.

    Idempotent: a document already analysed at the current prompt version is
    skipped unless `force` is set. Re-analysis replaces prior signals for the
    document rather than accumulating duplicates.
    """
    client = client or get_llm_client()
    signal_repo = SignalRepository(db)
    document_repo = DocumentRepository(db)

    document = document_repo.get_by_id(document_id)
    if document is None:
        logger.warning("Document %s not found.", document_id)
        return 0

    if not force and signal_repo.is_analyzed(document_id, PROMPT_VERSION):
        return 0

    company = CompanyRepository(db).get_by_id(document.company_id)
    ticker = company.ticker if company else "UNKNOWN"
    occurred_at = document.published_at or document.fetched_at

    try:
        result, used_sections = extraction.analyze(
            client,
            ticker=ticker,
            doc_subtype=document.doc_subtype,
            filed=str(occurred_at.date()),
            raw_text=_document_text(document.blob_uri),
        )
    except LLMUnavailableError:
        # A configuration problem is not this document's fault; let it
        # propagate so the batch stops instead of marking every document
        # FAILED for a reason that has nothing to do with their content.
        raise
    except Exception as exc:
        logger.exception("Analysis failed for document %s", document_id)
        signal_repo.record_analysis(
            document_id=document_id,
            prompt_version=PROMPT_VERSION,
            status=AnalysisStatus.FAILED,
            error=str(exc)[:500],
        )
        return 0

    if result is None:
        signal_repo.record_analysis(
            document_id=document_id,
            prompt_version=PROMPT_VERSION,
            status=AnalysisStatus.FAILED,
            error="structured output failed validation",
        )
        return 0

    signals = signal_writer.build_document_signals(
        result,
        company_id=document.company_id,
        document_id=document.id,
        occurred_at=occurred_at,
        used_sections=used_sections,
        doc_subtype=document.doc_subtype,
    )

    # A periodic filing is also compared against the previous one of its kind.
    # This must not be able to discard the primary analysis above: a transient
    # failure here (measured live, a request can time out on a large diff
    # prompt) should cost only the comparison, not the signals already built
    # from the filing itself, and must not crash a batch mid-run.
    if document.doc_subtype in COMPARISON_PLAN:
        try:
            signals += _comparison_signals(document, ticker, db, client)
        except LLMUnavailableError:
            raise
        except Exception:
            logger.exception(
                "Comparison against the prior filing failed for document %s; "
                "keeping the primary analysis's signals.",
                document_id,
            )

    signal_repo.delete_for_document(document.id)
    signal_repo.add_all(signals)
    signal_repo.record_analysis(
        document_id=document_id,
        prompt_version=PROMPT_VERSION,
        status=AnalysisStatus.COMPLETED,
        signal_count=len(signals),
    )
    logger.info("Analysed %s %s: %d signals", ticker, document.doc_subtype, len(signals))
    return len(signals)


def _comparison_signals(document, ticker: str, db: Session, client: LLMClient) -> list:
    """Compare a periodic filing against the previous filing of the same kind.

    Which section is compared depends on the cadence, see COMPARISON_PLAN: an
    annual report is worth diffing on its risk factors, a quarterly report on
    management's discussion of the quarter, because a 10-Q's risk section is
    usually a cross reference back to the 10-K rather than new material.
    """
    plan = COMPARISON_PLAN[document.doc_subtype]
    occurred_at = document.published_at or document.fetched_at

    prior = DocumentRepository(db).find_prior_filing(
        document.company_id, document.doc_subtype, occurred_at
    )
    if prior is None:
        return []

    changed, n_current, n_prior = diffing.find_changed_paragraphs(
        _document_text(document.blob_uri),
        _document_text(prior.blob_uri),
        section=plan["section"],
    )
    if not changed:
        logger.info(
            "%s %s: no unmatched paragraphs in Item %s (%d vs %d).",
            ticker, document.doc_subtype, plan["section"], n_current, n_prior,
        )
        return []

    logger.info(
        "%s %s: assessing %d changed paragraphs from Item %s.",
        ticker, document.doc_subtype, len(changed), plan["section"],
    )
    prior_period = str((prior.published_at or prior.fetched_at).date())

    # Each cadence gets its own prompt. Asking the risk-factor prompt
    # ("is this a substantive new risk?") about a gross-margin table produced
    # incoherent answers, because that is not what the passage is.
    if plan["kind"] == "quarter":
        result = client.parse(
            system=quarter_comparison.SYSTEM,
            user_content=quarter_comparison.build_user_content(
                ticker=ticker,
                current_period=str(occurred_at.date()),
                prior_period=prior_period,
                paragraphs=changed,
            ),
            schema=QuarterComparisonResult,
        )
        if result is None:
            return []
        return signal_writer.build_quarter_change_signals(
            result.changes,
            company_id=document.company_id,
            document_id=document.id,
            compared_document_id=prior.id,
            occurred_at=occurred_at,
        )

    result = client.parse(
        system=risk_diff.SYSTEM,
        user_content=risk_diff.build_user_content(
            ticker=ticker,
            current_year=str(occurred_at.date()),
            prior_year=prior_period,
            paragraphs=changed,
        ),
        schema=RiskDiffResult,
    )
    if result is None:
        return []

    return signal_writer.build_diff_signals(
        result.assessments,
        company_id=document.company_id,
        document_id=document.id,
        compared_document_id=prior.id,
        occurred_at=occurred_at,
        comparison=plan["comparison"],
    )


def select_recent_documents(ticker: str, db: Session) -> list[uuid.UUID]:
    """The bounded 'recent' scope: newest 10-K and 10-Q, recent 8-Ks, the two
    newest transcripts, and a capped slice of recent news.

    Each source type gets its own bound because they arrive at wildly different
    cadences. Treating them alike would let news, the noisiest and least
    individually valuable source, crowd out the filings.
    """
    company = CompanyRepository(db).get_by_ticker(ticker)
    if company is None:
        return []

    documents = DocumentRepository(db).list_timeline(company.id, limit=500)
    now = datetime.now(timezone.utc)
    filing_cutoff = now - timedelta(days=RECENT_8K_DAYS)

    def occurred(document) -> datetime:
        return document.published_at or document.fetched_at

    selected: list[uuid.UUID] = []
    for subtype in ("10-K", "10-Q"):
        newest = next((d for d in documents if d.doc_subtype == subtype), None)
        if newest is not None:
            selected.append(newest.id)

    selected += [
        d.id for d in documents
        if d.doc_subtype == "8-K" and occurred(d) >= filing_cutoff
    ]

    # list_timeline is newest-first, so slicing takes the most recent.
    selected += [
        d.id for d in documents if d.doc_subtype == "earnings_call"
    ][:MAX_TRANSCRIPTS]

    # News is deliberately absent: it is analysed as one batch per ticker by
    # select_recent_news/analyze_recent_news, not one call per item.
    return selected


def select_recent_news(ticker: str, db: Session) -> list:
    """Recent news documents for one ticker, newest first, capped.

    Returns documents rather than ids because the batch analysis needs their
    text and dates, and because a finding's position in the prompt is what maps
    it back to its source.
    """
    company = CompanyRepository(db).get_by_ticker(ticker)
    if company is None:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=RECENT_NEWS_DAYS)
    documents = DocumentRepository(db).list_timeline(company.id, limit=500)
    return [
        d for d in documents
        if d.doc_subtype == "news" and (d.published_at or d.fetched_at) >= cutoff
    ][:MAX_NEWS_DOCUMENTS]


def analyze_recent_news(
    ticker: str,
    db: Session,
    *,
    client: LLMClient | None = None,
    force: bool = False,
) -> int:
    """Analyse a ticker's recent news in a single call. Returns signals written.

    One call for the whole window rather than one per item. Items run a couple
    of hundred characters each, so per-item analysis was both the largest part
    of the engine's call budget and its least informative output; a run of
    coverage also says things no single item does.
    """
    documents = select_recent_news(ticker, db)
    if not documents:
        return 0

    signal_repo = SignalRepository(db)
    if not force and all(
        signal_repo.is_analyzed(d.id, PROMPT_VERSION) for d in documents
    ):
        return 0

    company = CompanyRepository(db).get_by_ticker(ticker)
    if company is None:
        return 0

    client = client or get_llm_client()
    items = [
        {
            "published": str((d.published_at or d.fetched_at).date()),
            "title": d.title or "",
            "text": _document_text(d.blob_uri),
        }
        for d in documents
    ]

    try:
        result = client.parse(
            system=news_digest.SYSTEM,
            user_content=news_digest.build_user_content(
                ticker=ticker, window_days=RECENT_NEWS_DAYS, items=items
            ),
            schema=NewsDigestResult,
        )
    except LLMUnavailableError:
        raise
    except Exception as exc:
        logger.exception("News digest failed for %s", ticker)
        for document in documents:
            signal_repo.record_analysis(
                document_id=document.id,
                prompt_version=PROMPT_VERSION,
                status=AnalysisStatus.FAILED,
                error=str(exc)[:500],
            )
        return 0

    if result is None:
        return 0

    # Drop findings whose quote is not actually in the item they claim, the
    # same receipt guarantee the per-document path gets from its own prompt.
    verified = []
    for finding in result.findings:
        index = finding.item_index - 1
        if 0 <= index < len(items) and finding.quote.strip() in items[index]["text"]:
            verified.append(finding)
        else:
            logger.info("%s: dropping a news finding whose quote did not match its item.", ticker)
    result = result.model_copy(update={"findings": verified})

    signals = signal_writer.build_news_signals(result, documents, company_id=company.id)

    for document in documents:
        signal_repo.delete_for_document(document.id)
    signal_repo.add_all(signals)
    for document in documents:
        signal_repo.record_analysis(
            document_id=document.id,
            prompt_version=PROMPT_VERSION,
            status=AnalysisStatus.COMPLETED,
            signal_count=sum(1 for s in signals if s.source_document_id == document.id),
        )

    logger.info(
        "Analysed %s news: %d items in one call, %d signals.",
        ticker, len(documents), len(signals),
    )
    return len(signals)


def regenerate_brief(ticker: str, db: Session):
    """Fold everything known about a company into its current read.

    Runs last, after every other stage has contributed its findings, and needs
    no model: see engine/brief.py for why the product's headline output is
    deliberately the one thing that never depends on a provider being up.
    """
    company = CompanyRepository(db).get_by_ticker(ticker)
    if company is None:
        return None

    brief_repo = BriefRepository(db)
    previous = brief_repo.latest_for(company.id)
    signals = SignalRepository(db).list_feed(company_id=company.id, limit=500)

    result = brief_engine.build_brief(
        signals,
        previous_generated_at=previous.generated_at if previous else None,
    )

    stored = brief_repo.create(
        company_id=company.id,
        stance=result.stance,
        headline=result.headline,
        confidence=result.confidence,
        drivers=[d.__dict__ for d in result.drivers],
        counterpoint=result.counterpoint.__dict__ if result.counterpoint else None,
        what_changed=result.what_changed,
        source_types=result.source_types,
        signal_count=result.signal_count,
        evidence=result.evidence,
        engine_version=brief_engine.ENGINE_VERSION,
    )
    logger.info("%s brief: %s (%d findings)", ticker, result.stance.value, result.signal_count)
    return stored


def evaluate_facts(ticker: str, db: Session) -> int:
    """Run the threshold rules over a company's structured facts.

    Takes no LLM client, because none of this needs one: the rules are
    arithmetic over already-structured data. That makes this the one analysis
    step that keeps working when the model provider is rate limited or
    unconfigured entirely.

    Prior rule-derived signals are replaced rather than accumulated, since a
    rule reports the current state of a window and re-running must not stack
    copies of the same finding.
    """
    company = CompanyRepository(db).get_by_ticker(ticker)
    if company is None:
        return 0

    facts = FactRepository(db).list_for_company(
        company.id,
        since=date.today() - timedelta(days=FACT_LOOKBACK_DAYS),
        limit=1000,
    )
    if not facts:
        return 0

    signal_repo = SignalRepository(db)
    findings = fact_rules.evaluate(facts)
    signal_repo.delete_untouched_rule_signals(company.id)

    if not findings:
        return 0

    signals = signal_writer.build_fact_signals(findings, company_id=company.id)
    signal_repo.add_all(signals)
    logger.info(
        "%s: %d fact rule(s) fired (%s).",
        ticker, len(signals), ", ".join(f.rule for f in findings),
    )
    return len(signals)


def synthesize_recent_shift(
    ticker: str,
    db: Session,
    *,
    client: LLMClient | None = None,
    window_days: int = clustering.DEFAULT_WINDOW_DAYS,
) -> int:
    """Look for a short-window pattern across a company's recent findings.

    Returns the number of signals written (0 or 1). Runs after per-document
    analysis, because it reasons over the signals that step produces rather
    than over documents.

    Costs nothing on a quiet week: the deterministic gate in clustering.py
    decides whether there is anything here worth a model call, and usually
    there is not.
    """
    company = CompanyRepository(db).get_by_ticker(ticker)
    if company is None:
        return 0

    signal_repo = SignalRepository(db)
    since = datetime.now(timezone.utc) - timedelta(days=PATTERN_LOOKBACK_DAYS)
    cluster = clustering.build_cluster(
        signal_repo.recent_for_company(company.id, since), window_days=window_days
    )

    if cluster is None:
        # Nothing qualifies now, so any pattern left from an earlier run is
        # stale and should not keep sitting at the top of the feed.
        signal_repo.delete_untouched_patterns(company.id)
        return 0

    anchor_document_id = cluster.anchor_signal.source_document_id
    existing = signal_repo.find_pattern_by_anchor(company.id, anchor_document_id)
    if existing is not None and (existing.reviewed_at or existing.dismissed_at):
        # The reader has already formed a judgment on this exact pattern.
        # Regenerating it would silently undo that.
        return 0

    client = client or get_llm_client()
    result = client.parse(
        system=emerging_pattern.SYSTEM,
        user_content=emerging_pattern.build_user_content(
            ticker=ticker, window_days=cluster.window_days, findings=cluster.as_findings()
        ),
        schema=EmergingPatternResult,
    )
    if result is None:
        return 0

    signal = signal_writer.build_pattern_signal(result, cluster, company_id=company.id)
    if signal is None:
        logger.info("%s: findings in the window were judged unrelated, no pattern written.", ticker)
        signal_repo.delete_untouched_patterns(company.id)
        return 0

    signal_repo.delete_untouched_patterns(company.id)
    signal_repo.add_all([signal])
    logger.info("%s: emerging pattern written (%s).", ticker, ", ".join(cluster.triggers))
    return 1


def analyze_company_recent(ticker: str, db: Session, *, force: bool = False) -> int:
    """Analyse the recent scope for one ticker. Returns signals written.

    One LLMClient is constructed for the whole batch and threaded through
    every document, so its cumulative token/cost counters *are* this run's
    totals by the time the loop ends, that's the natural place to persist
    them. Skipped when nothing was actually called (every document already
    analysed at this prompt version), so a no-op run doesn't clutter the
    usage history with a $0 / 0-token row.
    """
    client = get_llm_client()
    documents = select_recent_documents(ticker, db)
    total = 0
    for document_id in documents:
        total += analyze_document(document_id, db, client=client, force=force)

    # News is one batched call rather than one per item, so it sits outside the
    # loop above. Guarded separately: news is the least important source here,
    # and losing it must not cost the filing analysis already committed.
    try:
        total += analyze_recent_news(ticker, db, client=client, force=force)
    except LLMUnavailableError:
        raise
    except Exception:
        logger.exception("News analysis failed for %s; filing signals kept.", ticker)

    # Rules over structured facts need no model, so they run regardless of
    # what the provider is doing and are guarded only against their own bugs.
    try:
        total += evaluate_facts(ticker, db)
    except Exception:
        logger.exception("Fact rules failed for %s; other signals kept.", ticker)

    # Cross-document synthesis runs last, over the signals the loop just wrote.
    # Guarded separately for the same reason the year-over-year diff is: this
    # is the optional layer on top, and losing it must not cost the
    # per-document signals already committed.
    try:
        total += synthesize_recent_shift(ticker, db, client=client)
    except LLMUnavailableError:
        raise
    except Exception:
        logger.exception("Short-window synthesis failed for %s; per-document signals kept.", ticker)

    # The brief is the product's actual deliverable, so it is refreshed on
    # every run regardless of what any individual stage did or failed to do.
    try:
        regenerate_brief(ticker, db)
    except Exception:
        logger.exception("Brief regeneration failed for %s", ticker)

    if client.calls:
        UsageRepository(db).record(
            ticker=ticker,
            provider=type(client).__name__.replace("Client", "").lower(),
            model=client.model,
            calls=client.calls,
            input_tokens=client.input_tokens,
            output_tokens=client.output_tokens,
            cost_usd=client.cost_usd,
            documents_analyzed=len(documents),
        )

    return total
