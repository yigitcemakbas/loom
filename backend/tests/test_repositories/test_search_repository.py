"""Snippet-building tests.

Only the pure part is covered here: ranking is Postgres's job and is exercised
live rather than mocked, but snippet extraction is our own string handling and
is where an off-by-one silently ships a broken excerpt.
"""

from app.repositories.search_repository import SearchRepository

_CONTENT = (
    "Apple Inc. reported record June quarter revenue. "
    "The Company is experiencing a period of supply constraints and increasing costs "
    "for components driven by industry supply-demand imbalances. "
    "Management expects these trends to intensify through the coming year."
)


def test_snippet_centres_on_the_match():
    snippet = SearchRepository.snippet_for(_CONTENT, "supply constraints")

    assert snippet is not None
    assert "supply constraints" in snippet


def test_snippet_prefers_the_densest_cluster_of_terms():
    """The bug this guards against: filings repeat common words hundreds of
    times in financial tables, so anchoring on the first occurrence of any one
    term reliably landed the snippet on a wall of numbers instead of on the
    sentence the reader searched for."""
    content = (
        "Cost of sales: Products 47,153 43,620 Services 7,494 6,698 Total cost of sales 54,647. "
        + ("filler. " * 60)
        + "The Company believes gross margins will be subject to downward pressure."
    )
    snippet = SearchRepository.snippet_for(content, "gross margin downward pressure")

    assert snippet is not None
    assert "downward pressure" in snippet
    assert "47,153" not in snippet


def test_quoted_phrase_anchors_exactly():
    """A quoted phrase is an explicit instruction about what to look for and
    should beat the density heuristic."""
    content = (
        "Supply of components remains a topic. "
        + ("constraints on capital allocation. " * 20)
        + "We continue to experience supply constraints in advanced silicon."
    )
    snippet = SearchRepository.snippet_for(content, '"supply constraints"')

    assert snippet is not None
    assert "supply constraints in advanced silicon" in snippet


def test_snippet_marks_where_it_was_cut():
    long_content = ("filler words here. " * 200) + "the distinctive marker phrase" + (" trailing." * 200)
    snippet = SearchRepository.snippet_for(long_content, "distinctive")

    assert snippet is not None
    assert "distinctive" in snippet
    assert snippet.startswith("…") and snippet.endswith("…")


def test_snippet_is_whitespace_normalised():
    """Extracted filing text is full of hard-wrapped newlines; passing those
    through would render as a ragged block in the results table."""
    snippet = SearchRepository.snippet_for("alpha\n\n   beta\tgamma   delta", "beta")

    assert snippet is not None
    assert "\n" not in snippet
    assert "  " not in snippet


def test_short_terms_are_ignored():
    """Matching two-letter tokens would anchor the snippet on 'to' or 'of'
    rather than on what the reader searched for."""
    assert SearchRepository.snippet_for(_CONTENT, "of to a") is None


def test_no_snippet_when_the_term_is_absent():
    """Postgres matches stems, this scan matches literal words, so a ranked hit
    can legitimately have no locatable snippet. It must return None rather than
    invent one or crash."""
    assert SearchRepository.snippet_for(_CONTENT, "cryptocurrency") is None


def test_empty_inputs_are_handled():
    assert SearchRepository.snippet_for("", "anything") is None
    assert SearchRepository.snippet_for(_CONTENT, "") is None
