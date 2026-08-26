"""Section-extraction tests.

The fixtures below replicate three formatting variations found in filings
Loom has actually ingested. Each one broke a naive implementation:

  * Apple, title inline, one long line per paragraph
  * Alphabet, bare "ITEM 1A." with the title on the next line, and a
                 table-of-contents entry that looks identical to the header
  * Nasdaq, hard-wrapped at ~55 characters, so newline splitting yields
                 sentence fragments instead of paragraphs
"""

from app.engine.sections import extract_section, find_section_bounds, split_paragraphs

_BODY = (
    "The Company is exposed to risks from global economic conditions that "
    "could materially affect its results of operations and financial position."
)

# Apple style: title inline, preceded by a table of contents.
APPLE_STYLE = "\n".join(
    ["Item 1.", "Item 1A.", "Item 1B.", "Item 2."]          # contents block
    + ["Item 1.    Business", "Some business prose here."]
    + ["Item 1A.    Risk Factors"]
    + [f"{_BODY} Paragraph {i}." for i in range(30)]
    + ["Item 1B.    Unresolved Staff Comments", "No unresolved comments."]
)

# Alphabet style: bare header, title on the following line. The contents entry
# at the top is textually identical to the real header.
ALPHABET_STYLE = "\n".join(
    ["Item 1A.", "Item 1B.", "Item 2."]                      # contents block
    + ["ITEM 1A.", "RISK FACTORS"]
    + [f"{_BODY} Paragraph {i}." for i in range(30)]
    + ["ITEM 1B.", "UNRESOLVED STAFF COMMENTS"]
)

# Nasdaq style: real header inline, but body hard-wrapped mid-sentence.
NASDAQ_STYLE = "\n".join(
    ["Item 1A.", "Item 1B."]                                 # contents block
    + ["Item 1A. Risk Factors"]
    + ["The Company is exposed to significant risks arising from global", "economic conditions, regulatory change, and competitive pressure",
       "that could materially affect its results of operations."] * 15
    + ["Item 1B. Unresolved Staff Comments"]
)


def test_finds_section_with_inline_title():
    section = extract_section(APPLE_STYLE, "1A")
    assert section is not None
    assert "Paragraph 0." in section
    # Must stop at the next item, not run into it.
    assert "No unresolved comments." not in section


def test_finds_section_when_title_is_on_next_line():
    """The bare header must win over the identical contents entry."""
    section = extract_section(ALPHABET_STYLE, "1A")
    assert section is not None
    assert "Paragraph 0." in section
    assert "UNRESOLVED STAFF COMMENTS" not in section


def test_ignores_table_of_contents_entry():
    """The chosen header must be the real one, not the contents entry at the top."""
    bounds = find_section_bounds(APPLE_STYLE, "1A")
    assert bounds is not None
    start, _ = bounds
    # The contents entry sits in the first few lines; the real header is well below.
    assert start > 4


def test_returns_none_when_section_absent():
    assert extract_section("Item 1.  Business\nNothing else here.", "1A") is None


def test_paragraphs_from_unwrapped_filing():
    paragraphs = split_paragraphs(extract_section(APPLE_STYLE, "1A"))
    assert len(paragraphs) == 30
    assert all(p.endswith(".") for p in paragraphs)


def test_paragraphs_from_hard_wrapped_filing():
    """Wrapped lines must be rejoined into sentences, not left as fragments."""
    paragraphs = split_paragraphs(extract_section(NASDAQ_STYLE, "1A"))
    assert paragraphs, "hard-wrapped filing produced no paragraphs"
    # Each chunk should be a rejoined whole sentence, not a ~40-char fragment.
    assert all(p.endswith(".") for p in paragraphs)
    assert all(len(p) > 60 for p in paragraphs)


def test_short_headings_are_dropped():
    """Sub-headings carry no risk content and would pollute diffs."""
    text = APPLE_STYLE.replace("Item 1A.    Risk Factors", "Item 1A.    Risk Factors\nMacroeconomic Risks")
    assert "Macroeconomic Risks" not in split_paragraphs(extract_section(text, "1A"))
