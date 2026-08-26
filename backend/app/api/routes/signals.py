"""Thin routes over SignalRepository. No business logic here."""

from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from app.api.deps import CompanyRepo, DbSession, DocumentRepo, SignalRepo
from app.models.signal import SignalType
from app.schemas.signal import (
    AnalysisTriggerResponse,
    NoteRequest,
    SentimentPoint,
    SignalOut,
    SignalWithContext,
)
from app.scheduling.jobs import run_analysis
from app.engine.pipeline import select_recent_documents

router = APIRouter(tags=["signals"])


def with_context(signals, company_repo, document_repo) -> list[SignalWithContext]:
    """Attach ticker, source-document, and comparison details to each signal,
    so a card can show its full receipt without the client making extra
    requests."""
    out: list[SignalWithContext] = []
    for signal in signals:
        company = company_repo.get_by_id(signal.company_id)
        document = (
            document_repo.get_by_id(signal.source_document_id)
            if signal.source_document_id
            else None
        )
        compared = (
            document_repo.get_by_id(signal.compared_document_id)
            if signal.compared_document_id
            else None
        )
        metadata = signal.signal_metadata or {}
        document_ids = metadata.get("document_ids")
        out.append(
            SignalWithContext(
                **SignalOut.model_validate(signal).model_dump(),
                ticker=company.ticker if company else "?",
                source_url=document.source_url if document else None,
                doc_subtype=document.doc_subtype if document else None,
                compared_source_url=compared.source_url if compared else None,
                pattern_document_count=len(document_ids) if document_ids else None,
                pattern_window_days=metadata.get("window_days"),
            )
        )
    return out


@router.get("/signals", response_model=list[SignalWithContext])
def list_signals(
    signal_repo: SignalRepo,
    company_repo: CompanyRepo,
    document_repo: DocumentRepo,
    ticker: str | None = None,
    signal_type: SignalType | None = None,
    since: datetime | None = None,
    min_confidence: float | None = Query(default=None, ge=0.0, le=1.0),
    unreviewed_only: bool = False,
    limit: int = Query(default=100, le=500),
):
    """Ranked cross-ticker feed, highest priority first. Dismissed signals
    are excluded (see SignalRepository.list_feed)."""
    company_id = None
    if ticker:
        company = company_repo.get_by_ticker(ticker)
        if company is None:
            raise HTTPException(status_code=404, detail=f"Unknown ticker {ticker!r}")
        company_id = company.id

    signals = signal_repo.list_feed(
        company_id=company_id,
        signal_type=signal_type,
        since=since,
        min_confidence=min_confidence,
        unreviewed_only=unreviewed_only,
        limit=limit,
    )
    return with_context(signals, company_repo, document_repo)


@router.get("/signals/{signal_id}", response_model=SignalWithContext)
def get_signal(
    signal_id: str,
    signal_repo: SignalRepo,
    company_repo: CompanyRepo,
    document_repo: DocumentRepo,
):
    signal = signal_repo.get_by_id(signal_id)
    if signal is None:
        raise HTTPException(status_code=404, detail="Signal not found")
    return with_context([signal], company_repo, document_repo)[0]


@router.post("/signals/{signal_id}/note", response_model=SignalWithContext)
def annotate_signal(
    signal_id: str,
    body: NoteRequest,
    signal_repo: SignalRepo,
    company_repo: CompanyRepo,
    document_repo: DocumentRepo,
):
    """Writing a note is what marks a signal reviewed (see SignalRepository.set_note)."""
    signal = signal_repo.set_note(signal_id, body.note)
    if signal is None:
        raise HTTPException(status_code=404, detail="Signal not found")
    return with_context([signal], company_repo, document_repo)[0]


@router.post("/signals/{signal_id}/dismiss", response_model=SignalWithContext)
def dismiss_signal(signal_id: str, signal_repo: SignalRepo, company_repo: CompanyRepo, document_repo: DocumentRepo):
    signal = signal_repo.mark_dismissed(signal_id)
    if signal is None:
        raise HTTPException(status_code=404, detail="Signal not found")
    return with_context([signal], company_repo, document_repo)[0]


@router.get("/companies/{ticker}/sentiment", response_model=list[SentimentPoint])
def get_sentiment_series(ticker: str, signal_repo: SignalRepo, company_repo: CompanyRepo):
    company = company_repo.get_by_ticker(ticker)
    if company is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker {ticker!r}")
    return [
        SentimentPoint(
            occurred_at=s.occurred_at, sentiment_score=s.sentiment_score, summary=s.summary
        )
        for s in signal_repo.sentiment_series(company.id)
    ]


@router.post("/admin/analyze/{ticker}", response_model=AnalysisTriggerResponse)
def trigger_analysis(
    ticker: str,
    background_tasks: BackgroundTasks,
    db: DbSession,
    company_repo: CompanyRepo,
    force: bool = False,
):
    """Queue analysis of a ticker's recent filings, run in the background."""
    company = company_repo.get_by_ticker(ticker)
    if company is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker {ticker!r}")

    queued = len(select_recent_documents(ticker.upper(), db))
    background_tasks.add_task(run_analysis, ticker.upper(), force)
    return AnalysisTriggerResponse(
        ticker=ticker.upper(),
        documents_queued=queued,
        detail="Analysis started. Signals appear as each document finishes.",
    )
