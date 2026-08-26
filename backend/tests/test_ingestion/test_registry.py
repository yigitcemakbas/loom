"""Unit tests for the pure-function pieces of ingestion/registry.py, the
content-hash dedupe key that the Phase 1 verification steps rely on.
"""

from app.ingestion.registry import _content_hash


def test_content_hash_is_deterministic():
    assert _content_hash("hello world") == _content_hash("hello world")


def test_content_hash_differs_for_different_content():
    assert _content_hash("filing A") != _content_hash("filing B")
