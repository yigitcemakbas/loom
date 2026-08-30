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
    right choice for a single-machine MVP.

    URIs are stored **relative** to `root_dir` (`file://AAPL/<hash>.txt`) and
    resolved against whatever `root_dir` is configured at read time. Storing
    the absolute path instead, which this originally did, silently welded the
    database to one directory on one machine: moving the checkout, running in
    a container, or restoring the data anywhere else left every document
    unreadable while the rows still looked perfectly valid.

    Absolute URIs written by that earlier version are still honoured, and
    re-rooted under the current directory when the original path is gone, so
    existing data keeps working without a migration.
    """

    def __init__(self, root_dir: Path):
        self.root_dir = root_dir
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, content: bytes, content_type: str = "text/plain") -> str:
        path = self.root_dir / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        # Relative on purpose, see the class docstring.
        return f"file://{key}"

    def get(self, uri: str) -> bytes:
        if not uri.startswith("file://"):
            raise ValueError(f"LocalFileBlobStore cannot resolve non-file URI: {uri}")
        return self._resolve(uri[len("file://") :]).read_bytes()

    def _resolve(self, path_part: str) -> Path:
        path = Path(path_part)
        if not path.is_absolute():
            return self.root_dir / path
        if path.exists():
            return path
        # An absolute URI written on another machine, or before this store
        # went relative. The trailing "<TICKER>/<hash>.txt" is the real key,
        # so re-root it under the directory in use now.
        return self.root_dir.joinpath(*path.parts[-2:])


@lru_cache
def get_blob_store() -> BlobStore:
    """The single place that decides which BlobStore implementation is active.

    Swapping to S3BlobStore later means changing only this function.
    """
    return LocalFileBlobStore(settings.blob_store_dir)
