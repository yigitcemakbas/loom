"""Deterministic extraction of named sections from SEC filings.

No LLM involved: locating "Item 1A. Risk Factors" inside a filing is a
structural problem, and structure is what deterministic code is good at.
This matters for more than purity, an average 10-K is ~98k tokens and the
risk-factor section is a fraction of that, so extracting the section first
cuts LLM input several-fold and strips boilerplate that dilutes quality.

Two real formatting variations, both observed in filings Loom has stored,
drive the design here:

1. Where the section title lives. Apple writes "Item 1A.    Risk Factors"
   on one line, while Alphabet writes a bare "ITEM 1A." with the title on
   the following line, and Alphabet's table-of-contents entry is *also* a
   bare "Item 1A.". Neither the title nor the item number alone can tell a
   real header from a contents entry, so instead we pick the candidate that
   actually has a section's worth of text behind it. A contents entry is
   followed by the next contents entry within a few lines; the real header
   is followed by hundreds.

2. How text is wrapped. Apple emits one long line per paragraph; Nasdaq
   hard-wraps at roughly 55 characters, so splitting on newlines there
   yields sentence fragments rather than paragraphs. Lines are rejoined
   until sentence-ending punctuation, which leaves Apple's paragraphs
   intact and reassembles Nasdaq's into whole sentences.
"""

import re

# "Item 1A." / "ITEM 1A" / "Item 1A -", optionally followed by a title.
# Group 1 = item number ("1A"), group 2 = title text when present.
# \s covers the non-breaking spaces (\xa0) some filers emit.
_ITEM_HEADER = re.compile(
    r"^\s*item\s+(\d+[A-C]?)\s*[.\-–—:]?\s*(.*?)\s*$",
    re.IGNORECASE,
)

# Page furniture repeated on every page, e.g. "Apple Inc. | 2025 Form 10-K | 5".
_PAGE_FOOTER = re.compile(r"^.{0,80}\|.{0,60}\|\s*\d+\s*$")

# Bare page numbers left behind by the HTML-to-text pass.
_BARE_PAGE_NUMBER = re.compile(r"^\s*\d{1,4}\s*$")

_SENTENCE_END = (".", "?", "!", '"', "”", "’")

# A real section must have at least this many lines of body text. Tuned to sit
# far above a table-of-contents entry (a handful of lines) and far below a
# genuine section (hundreds).
_MIN_SECTION_LINES = 20


def _header_at(line: str) -> tuple[str, str] | None:
    """Return (item_number, title) when a line looks like an Item header."""
    match = _ITEM_HEADER.match(line)
    if not match:
        return None
    return match.group(1).upper(), match.group(2)


def find_section_bounds(text: str, item: str) -> tuple[int, int] | None:
    """Return (start_line, end_line) for `item`, or None if not found.

    Every candidate header for the item is scored by how much text follows it
    before the next Item header, and the largest wins. That single rule
    handles both title-inline and title-on-next-line filings, and rejects
    table-of-contents entries and prose cross-references without needing to
    recognise them explicitly.
    """
    lines = text.splitlines()
    target = item.upper()

    candidates = [
        i for i, line in enumerate(lines)
        if (header := _header_at(line)) is not None and header[0] == target
    ]
    if not candidates:
        return None

    best: tuple[int, int] | None = None
    best_size = 0
    for start in candidates:
        end = len(lines)
        for j in range(start + 1, len(lines)):
            header = _header_at(lines[j])
            if header is not None and header[0] != target:
                end = j
                break
        size = end - start
        if size > best_size:
            best_size, best = size, (start, end)

    if best is None or best_size < _MIN_SECTION_LINES:
        return None
    return best


def _clean(lines: list[str]) -> list[str]:
    """Drop page furniture that survives HTML-to-text conversion."""
    return [
        line for line in lines
        if line.strip()
        and not _PAGE_FOOTER.match(line)
        and not _BARE_PAGE_NUMBER.match(line)
    ]


def extract_section(text: str, item: str) -> str | None:
    """Return the text of a filing section, or None when it isn't present."""
    bounds = find_section_bounds(text, item)
    if bounds is None:
        return None
    start, end = bounds
    body = _clean(text.splitlines()[start + 1 : end])
    if not body:
        return None
    return "\n".join(body)


def split_paragraphs(section_text: str, min_chars: int = 120) -> list[str]:
    """Split a section into comparable chunks for diffing.

    Lines are rejoined until sentence-ending punctuation so that hard-wrapped
    filings produce whole sentences rather than fragments. Chunks shorter than
    `min_chars` (sub-headings such as "Macroeconomic and Industry Risks",
    stray table cells) are dropped, they carry no risk content and would
    otherwise show up as noisy false "new risk" matches.

    Granularity differs between filers, one long paragraph for Apple, one
    sentence for Nasdaq, which is fine: diffing only ever compares two
    filings from the same company, and a given filer's formatting is stable
    year to year.
    """
    chunks: list[str] = []
    buffer: list[str] = []
    for line in section_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        buffer.append(stripped)
        if stripped.endswith(_SENTENCE_END):
            chunks.append(" ".join(buffer))
            buffer = []
    if buffer:
        chunks.append(" ".join(buffer))

    return [chunk for chunk in chunks if len(chunk) >= min_chars]
