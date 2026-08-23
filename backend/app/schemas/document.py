import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.document import SourceType


class RawDocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    source_type: SourceType
    source_name: str
    source_url: str | None
    doc_subtype: str | None
    title: str | None
    published_at: datetime | None
    fetched_at: datetime


class RawDocumentDetail(RawDocumentOut):
    """Includes the actual content, fetched from the BlobStore on demand."""

    content: str
