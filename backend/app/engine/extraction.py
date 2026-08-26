"""Per-document analysis: prepare the text, make one structured call.

Section selection happens here rather than in the prompt. A 10-K averages
~98k tokens, but sentiment lives in Management's Discussion (Item 7) and risk
lives in Item 1A. Sending only those cuts input several-fold and removes
boilerplate that otherwise dilutes the model's attention.
"""

import logging

from app.engine.llm_client import LLMClient
from app.engine.prompts import document_analysis
from app.engine.prompts.document_analysis import DocumentAnalysisResult
from app.engine.sections import extract_section

logger = logging.getLogger(__name__)

# Sections worth reading, per filing type. Item 1A is risk factors, Item 7 is
# Management's Discussion and Analysis.
_SECTIONS_BY_SUBTYPE: dict[str, list[str]] = {
    "10-K": ["1A", "7"],
    "10-Q": ["1A", "2"],   # in a 10-Q, MD&A is Item 2
}


def prepare_text(raw_text: str, doc_subtype: str | None) -> tuple[str, bool]:
    """Return (text_for_analysis, used_sections).

    `used_sections` is False when we fell back to the whole document, which
    the caller records so a signal derived from unfocused input can be
    discounted rather than trusted equally.
    """
    wanted = _SECTIONS_BY_SUBTYPE.get(doc_subtype or "")
    if not wanted:
        # 8-K and friends are short and unstructured; the whole document is
        # both cheap and the right input.
        return raw_text, False

    parts: list[str] = []
    for item in wanted:
        section = extract_section(raw_text, item)
        if section:
            parts.append(f"=== Item {item} ===\n{section}")

    if not parts:
        logger.warning(
            "No expected sections found in a %s; analysing the full document instead.",
            doc_subtype,
        )
        return raw_text, False

    return "\n\n".join(parts), True


def analyze(
    client: LLMClient,
    *,
    ticker: str,
    doc_subtype: str | None,
    filed: str,
    raw_text: str,
) -> tuple[DocumentAnalysisResult | None, bool]:
    """Analyse one document. Returns (result, used_sections)."""
    text, used_sections = prepare_text(raw_text, doc_subtype)
    result = client.parse(
        system=document_analysis.SYSTEM,
        user_content=document_analysis.build_user_content(
            ticker=ticker, doc_subtype=doc_subtype or "filing", filed=filed, text=text
        ),
        schema=DocumentAnalysisResult,
    )
    return result, used_sections
