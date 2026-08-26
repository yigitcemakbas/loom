"""Raw content persistence, isolated behind an interface.

This is the concrete seam described in docs/plan.md "Storage Design": the
relational layer (Postgres, via app/repositories/) stores structured
metadata and queryable columns; large raw content (filing text, transcript
JSON, article HTML) is a separate concern, stored as an object and
referenced only by a URI. `LocalFileBlobStore` is the Phase 1 (and beyond,
until it's genuinely needed) implementation, a future `S3BlobStore` can
implement the same interface with zero changes to ingestion adapters or
the engine.
"""

from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path

from app.config import settings


class BlobStore(ABC):
    @abstractmethod
    def put(self, key: str, content: bytes, content_type: str = "text/plain") -> str:
        """Persist content under `key`, returning a URI that `get()` can resolve."""
        ...

    @abstractmethod
    def get(self, uri: str) -> bytes:
        """Retrieve content previously stored at `uri`."""
        ...


class LocalFileBlobStore(BlobStore):
    """Writes blobs to a directory on disk. Zero operational overhead, the
    right choice for a single-machine MVP. `uri` is a `file://` path relative
    to `root_dir`.
    """

    def __init__(self, root_dir: Path):
        self.root_dir = root_dir
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, content: bytes, content_type: str = "text/plain") -> str:
        path = self.root_dir / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return f"file://{path.resolve()}"

    def get(self, uri: str) -> bytes:
        if not uri.startswith("file://"):
            raise ValueError(f"LocalFileBlobStore cannot resolve non-file URI: {uri}")
        path = Path(uri[len("file://") :])
        return path.read_bytes()


@lru_cache
def get_blob_store() -> BlobStore:
    """The single place that decides which BlobStore implementation is active.

    Swapping to S3BlobStore later means changing only this function.
    """
    return LocalFileBlobStore(settings.blob_store_dir)
