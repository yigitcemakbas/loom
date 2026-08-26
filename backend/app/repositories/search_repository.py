"""The only module that queries `document_search_index`.

Search is two steps on purpose. Postgres ranks matches from the tsvector, which
is fast and touches no document content at all; only the handful of rows
actually being returned then have their text read back from the BlobStore to
build a snippet. That is what lets full-text search exist without the database
holding a second copy of every filing.
"""

import re
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.document import RawDocument
from app.models.search import DocumentSearchIndex

# Long enough to read as a sentence, short enough that ten results still fit
# on one screen.
_SNIPPET_RADIUS = 160

# tsvector values are capped at 1MB by Postgres. Filings run well under this
# once extracted, but a pathological document must not be able to fail an
# entire ingest, so the input is bounded before it reaches to_tsvector.
_MAX_INDEXABLE_CHARS = 900_000

# Common words recur hundreds of times in a filing's tables. Scanning every
# occurrence to place one snippet would make rendering results cost more than
# the search itself.
_MAX_OCCURRENCES_PER_TERM = 400


@dataclass
class SearchHit:
    document: RawDocument
    rank: float
    snippet: str | None


class SearchRepository:
    def __init__(self, db: Session):
        self.db = db

    def index_document(self, document_id: uuid.UUID, *, title: str | None, content: str) -> None:
        """Insert or replace one document's search vector.

        Title terms are weighted above body terms so that searching a company's
        own name returns filings titled for it, not every filing that mentions
        it in passing.
        """
        content = content[:_MAX_INDEXABLE_CHARS]
        self.db.execute(
            text(
                """
                INSERT INTO document_search_index (document_id, search_vector, content_chars)
                VALUES (
                    :document_id,
                    setweight(to_tsvector('english', coalesce(:title, '')), 'A') ||
                    setweight(to_tsvector('english', :content), 'B'),
                    :content_chars
                )
                ON CONFLICT (document_id) DO UPDATE SET
                    search_vector = EXCLUDED.search_vector,
                    content_chars = EXCLUDED.content_chars,
                    indexed_at = now()
                """
            ),
            {
                "document_id": str(document_id),
                "title": title,
                "content": content,
                "content_chars": len(content),
            },
        )
        self.db.commit()

    def is_indexed(self, document_id: uuid.UUID) -> bool:
        stmt = select(DocumentSearchIndex.document_id).where(
            DocumentSearchIndex.document_id == document_id
        )
        return self.db.execute(stmt).scalar_one_or_none() is not None

    def indexed_count(self) -> int:
        return self.db.execute(select(func.count(DocumentSearchIndex.document_id))).scalar() or 0

    def search(
        self,
        query: str,
        *,
        company_id: uuid.UUID | None = None,
        doc_subtype: str | None = None,
        limit: int = 25,
    ) -> list[tuple[RawDocument, float]]:
        """Ranked matches, best first. Returns no content, see `snippet_for`.

        Uses websearch_to_tsquery, which accepts what a person would actually
        type ("margin pressure", quoted phrases, `or`, `-excluded`) instead of
        the operator syntax plainto_/to_tsquery demand.
        """
        if not query.strip():
            return []

        tsquery = func.websearch_to_tsquery("english", query)
        rank = func.ts_rank(DocumentSearchIndex.search_vector, tsquery)

        stmt = (
            select(RawDocument, rank.label("rank"))
            .join(DocumentSearchIndex, DocumentSearchIndex.document_id == RawDocument.id)
            .where(DocumentSearchIndex.search_vector.op("@@")(tsquery))
        )
        if company_id is not None:
            stmt = stmt.where(RawDocument.company_id == company_id)
        if doc_subtype is not None:
            stmt = stmt.where(RawDocument.doc_subtype == doc_subtype)

        stmt = stmt.order_by(rank.desc(), RawDocument.published_at.desc().nulls_last()).limit(limit)
        return [(row[0], float(row[1])) for row in self.db.execute(stmt).all()]

    @staticmethod
    def snippet_for(content: str, query: str) -> str | None:
        """A window of the document around the best cluster of query terms.

        Built here rather than with Postgres `ts_headline`, which would require
        the full text to live in the database, the thing this design avoids.

        Anchoring matters more than it sounds. Filings repeat common words like
        "margin" and "cost" hundreds of times in financial tables, so jumping to
        the first occurrence of any single term reliably lands on a wall of
        numbers rather than on the passage the reader searched for. Instead every
        occurrence is scored by how many *distinct* query terms sit within a
        window of it, and the densest window wins. An exact quoted phrase, if
        present, beats all of it.
        """
        if not content or not query.strip():
            return None

        def window_at(position: int) -> str:
            start = max(0, position - _SNIPPET_RADIUS)
            end = min(len(content), position + _SNIPPET_RADIUS)
            body = " ".join(content[start:end].split())
            return f"{'…' if start else ''}{body}{'…' if end < len(content) else ''}"

        lowered = content.lower()

        # A quoted phrase is an explicit instruction about what to look for.
        for phrase in re.findall(r'"([^"]+)"', query):
            found = lowered.find(phrase.strip().lower())
            if found != -1:
                return window_at(found)

        terms = {t for t in re.findall(r"\w+", query.lower()) if len(t) > 2}
        if not terms:
            return None

        # Occurrences per term, bounded so a term appearing thousands of times
        # cannot turn snippet-building into the expensive part of a search.
        occurrences: list[tuple[int, str]] = []
        for term in terms:
            start = 0
            for _ in range(_MAX_OCCURRENCES_PER_TERM):
                found = lowered.find(term, start)
                if found == -1:
                    break
                occurrences.append((found, term))
                start = found + len(term)
        if not occurrences:
            return None

        occurrences.sort()
        positions = [p for p, _ in occurrences]

        best_position, best_score = positions[0], 0
        for index, (position, _term) in enumerate(occurrences):
            nearby = {occurrences[index][1]}
            for other in range(index + 1, len(occurrences)):
                if positions[other] - position > _SNIPPET_RADIUS:
                    break
                nearby.add(occurrences[other][1])
            if len(nearby) > best_score:
                best_position, best_score = position, len(nearby)
                if best_score == len(terms):
                    break  # every term present, nothing can beat this

        return window_at(best_position)
