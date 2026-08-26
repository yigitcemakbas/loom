"""The only module allowed to run SQL/ORM queries against `raw_documents`.

Note what this repository does *not* do: it never reads or writes blob
content. That's BlobStore's job (app/storage/blob_store.py). This
repository only persists/queries the metadata row and its `blob_uri`
pointer, keeping "store the bytes" and "query the metadata" genuinely
separate concerns, per docs/plan.md "Storage Design".
"""

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.document import RawDocument, SourceType


class DocumentRepository:
    def __init__(self, db: Session):
        self.db = db

    def exists(self, company_id: uuid.UUID, content_hash: str) -> bool:
        stmt = select(RawDocument.id).where(
            RawDocument.company_id == company_id,
            RawDocument.content_hash == content_hash,
        )
        return self.db.execute(stmt).scalar_one_or_none() is not None

    def exists_by_source_url(self, company_id: uuid.UUID, source_url: str) -> bool:
        """Identity check that survives changes to how content is extracted.

        content_hash answers "have I stored these exact bytes", which is the
        wrong question whenever extraction improves. Adding 8-K exhibits
        changed the bytes of every earnings 8-K, and on a content_hash-only
        check each one would have been re-ingested as a second row beside its
        own cover sheet. A filing's identity is its URL.

        The tradeoff is that content republished at the same URL is no longer
        re-ingested. For SEC that is correct, amendments get their own
        accession and therefore their own URL; for news it means an article
        silently edited after publication keeps the version first seen, which
        is the more useful behaviour for an audit trail anyway.
        """
        stmt = select(RawDocument.id).where(
            RawDocument.company_id == company_id,
            RawDocument.source_url == source_url,
        )
        return self.db.execute(stmt).scalar_one_or_none() is not None

    def create(
        self,
        *,
        company_id: uuid.UUID,
        source_type: SourceType,
        source_name: str,
        source_url: str | None,
        doc_subtype: str | None,
        title: str | None,
        published_at: datetime | None,
        blob_uri: str,
        content_hash: str,
        doc_metadata: dict | None = None,
    ) -> RawDocument | None:
        """Returns None (instead of raising) if this exact document was
        already ingested for this company, the content_hash dedupe path
        the Phase 1 verification steps exercise deliberately.
        """
        doc = RawDocument(
            company_id=company_id,
            source_type=source_type,
            source_name=source_name,
            source_url=source_url,
            doc_subtype=doc_subtype,
            title=title,
            published_at=published_at,
            blob_uri=blob_uri,
            content_hash=content_hash,
            doc_metadata=doc_metadata or {},
        )
        self.db.add(doc)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            return None
        self.db.refresh(doc)
        return doc

    def get_by_id(self, document_id: uuid.UUID) -> RawDocument | None:
        return self.db.get(RawDocument, document_id)

    def update_content(
        self,
        document_id: uuid.UUID,
        *,
        blob_uri: str,
        content_hash: str,
        doc_metadata: dict | None = None,
    ) -> RawDocument | None:
        """Repoint an existing document at newly fetched content.

        Needed when the *same* filing is re-fetched more completely than before
        rather than for the first time: the 8-K exhibit work (see
        ingestion/sec_edgar.py) changed what "the whole document" means, and
        without this every affected 8-K would be re-ingested as a second row
        beside its own cover sheet, since dedupe keys on content_hash.
        """
        document = self.get_by_id(document_id)
        if document is None:
            return None
        document.blob_uri = blob_uri
        document.content_hash = content_hash
        if doc_metadata is not None:
            document.doc_metadata = doc_metadata
        self.db.commit()
        self.db.refresh(document)
        return document

    def list_timeline(self, company_id: uuid.UUID, limit: int = 100) -> list[RawDocument]:
        stmt = (
            select(RawDocument)
            .where(RawDocument.company_id == company_id)
            .order_by(RawDocument.published_at.desc().nulls_last())
            .limit(limit)
        )
        return list(self.db.execute(stmt).scalars().all())

    def list_all(
        self,
        *,
        company_id: uuid.UUID | None = None,
        doc_subtype: str | None = None,
        limit: int = 200,
    ) -> list[RawDocument]:
        """Cross-portfolio browse, list_timeline above is scoped to one
        company; this is the query the Filings Browser needs."""
        stmt = select(RawDocument)
        if company_id is not None:
            stmt = stmt.where(RawDocument.company_id == company_id)
        if doc_subtype is not None:
            stmt = stmt.where(RawDocument.doc_subtype == doc_subtype)
        stmt = stmt.order_by(RawDocument.published_at.desc().nulls_last()).limit(limit)
        return list(self.db.execute(stmt).scalars().all())

    def newest_published_at(self, company_id: uuid.UUID) -> datetime | None:
        """Timestamp of the company's most recent document, or None if it has
        none yet. Drives incremental ingestion, see registry.ingest_all."""
        stmt = select(func.max(RawDocument.published_at)).where(
            RawDocument.company_id == company_id
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def find_prior_filing(
        self, company_id: uuid.UUID, doc_subtype: str, before: datetime
    ) -> RawDocument | None:
        """Used by engine/diffing.py (Phase 2+) to find the prior same-subtype
        filing to diff a new one against.
        """
        stmt = (
            select(RawDocument)
            .where(
                RawDocument.company_id == company_id,
                RawDocument.doc_subtype == doc_subtype,
                RawDocument.published_at < before,
            )
            .order_by(RawDocument.published_at.desc())
            .limit(1)
        )
        return self.db.execute(stmt).scalar_one_or_none()
