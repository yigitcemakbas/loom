"""The only module allowed to run SQL/ORM queries against `raw_documents`.

Note what this repository does *not* do: it never reads or writes blob
content. That's BlobStore's job (app/storage/blob_store.py). This
repository only persists/queries the metadata row and its `blob_uri`
pointer — keeping "store the bytes" and "query the metadata" genuinely
separate concerns, per docs/plan.md "Storage Design".
"""

import uuid
from datetime import datetime

from sqlalchemy import select
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
        already ingested for this company — the content_hash dedupe path
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

    def list_timeline(self, company_id: uuid.UUID, limit: int = 100) -> list[RawDocument]:
        stmt = (
            select(RawDocument)
            .where(RawDocument.company_id == company_id)
            .order_by(RawDocument.published_at.desc().nulls_last())
            .limit(limit)
        )
        return list(self.db.execute(stmt).scalars().all())

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
