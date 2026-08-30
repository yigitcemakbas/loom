"""BlobStore portability tests.

The bug these pin down shipped silently and only surfaced when the app was
containerised: URIs were stored as absolute host paths, so the database was
welded to one directory on one machine. Every row still looked valid while
every document was unreadable.
"""

from pathlib import Path

import pytest

from app.storage.blob_store import LocalFileBlobStore


def test_round_trip(tmp_path: Path):
    store = LocalFileBlobStore(tmp_path)
    uri = store.put(key="AAPL/abc123.txt", content=b"filing text")

    assert store.get(uri) == b"filing text"


def test_uri_is_relative_to_the_store_root(tmp_path: Path):
    """The whole point: a stored URI must not name a machine-specific path."""
    store = LocalFileBlobStore(tmp_path)
    uri = store.put(key="AAPL/abc123.txt", content=b"filing text")

    assert uri == "file://AAPL/abc123.txt"
    assert str(tmp_path) not in uri


def test_data_survives_the_directory_moving(tmp_path: Path):
    """Containerising, relocating the checkout, or restoring a backup all look
    like this: same content, different absolute path."""
    original = tmp_path / "original"
    store = LocalFileBlobStore(original)
    uri = store.put(key="AAPL/abc123.txt", content=b"filing text")

    moved = tmp_path / "moved"
    original.rename(moved)

    assert LocalFileBlobStore(moved).get(uri) == b"filing text"


def test_legacy_absolute_uris_still_resolve(tmp_path: Path):
    """Rows written before the store went relative must keep working without
    a migration."""
    store = LocalFileBlobStore(tmp_path)
    store.put(key="AAPL/abc123.txt", content=b"filing text")

    legacy = "file:///somewhere/else/entirely/data/blobs/AAPL/abc123.txt"

    assert store.get(legacy) == b"filing text"


def test_absolute_uri_that_does_exist_is_used_as_given(tmp_path: Path):
    external = tmp_path / "external"
    external.mkdir()
    (external / "note.txt").write_bytes(b"external content")

    store = LocalFileBlobStore(tmp_path / "root")

    assert store.get(f"file://{external / 'note.txt'}") == b"external content"


def test_non_file_uri_is_rejected(tmp_path: Path):
    """A URI from a different backend must fail loudly, not be misread as a
    local path."""
    store = LocalFileBlobStore(tmp_path)

    with pytest.raises(ValueError):
        store.get("s3://bucket/key.txt")
