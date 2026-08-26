"""Section-selection tests for the analysis input.

Which sections get sent is the main cost lever in the engine, so it is worth
asserting rather than assuming.
"""

from app.engine.extraction import prepare_text

FILING = "\n".join(
    ["Item 1A.", "Item 7."]
    + ["Item 1A.    Risk Factors"]
    + [f"A substantive risk paragraph number {i} long enough to survive filtering here." for i in range(25)]
    + ["Item 7.    Management's Discussion and Analysis"]
    + [f"A substantive discussion paragraph number {i} long enough to survive filtering." for i in range(25)]
    + ["Item 8.    Financial Statements", "Tables follow."]
)


def test_10k_uses_only_the_relevant_sections():
    text, used_sections = prepare_text(FILING, "10-K")
    assert used_sections
    assert "risk paragraph" in text
    assert "discussion paragraph" in text
    # The point of section selection: unrelated bulk is excluded.
    assert "Tables follow." not in text
    assert len(text) < len(FILING)


def test_8k_falls_back_to_the_whole_document():
    """8-Ks are short and unstructured; the whole document is the right input."""
    text, used_sections = prepare_text("Short announcement text.", "8-K")
    assert not used_sections
    assert text == "Short announcement text."


def test_unparseable_filing_falls_back_rather_than_failing():
    text, used_sections = prepare_text("No recognisable sections at all.", "10-K")
    assert not used_sections
    assert text == "No recognisable sections at all."
