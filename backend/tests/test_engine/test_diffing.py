"""Diffing tests.

The threshold matters commercially: too low and every annual reword becomes a
false "new risk", too high and real changes are missed. These lock in the
behaviour measured against real filings, where the similarity distribution was
clearly bimodal around 0.75.
"""

from app.engine.diffing import MAX_PARAGRAPHS_TO_ASSESS, best_similarity, find_changed_paragraphs

_RISK = (
    "The Company depends on a limited number of suppliers for critical components, "
    "and disruption at any one of them could materially harm its operating results."
)


def test_identical_text_matches():
    assert best_similarity(_RISK, [_RISK]) == 1.0


def test_cosmetic_rewording_still_matches():
    """Filers lightly reword boilerplate every year; that is not a new risk."""
    reworded = _RISK.replace("The Company", "the company").replace(",", "") + "!"
    assert best_similarity(reworded, [_RISK]) >= 0.75


def test_genuinely_different_risk_does_not_match():
    other = (
        "New export controls on advanced semiconductors could restrict the Company's "
        "ability to sell its products into certain international markets."
    )
    assert best_similarity(other, [_RISK]) < 0.75


def test_missing_section_yields_no_diff():
    """A filing without an extractable Item 1A must degrade quietly, not raise."""
    changed, n_cur, n_pri = find_changed_paragraphs("no sections here", "nor here")
    assert changed == [] and n_cur == 0 and n_pri == 0


def _filing(paragraphs: list[str]) -> str:
    return "\n".join(
        ["Item 1A.", "Item 1B."]
        + ["Item 1A.    Risk Factors"]
        + paragraphs
        + ["Item 1B.    Unresolved Staff Comments"]
    )


def test_only_unmatched_paragraphs_are_returned():
    shared = [f"{_RISK} Shared paragraph number {i} with enough length to count." for i in range(25)]
    new_risk = (
        "A newly disclosed dependency on a single logistics provider in Asia could "
        "interrupt distribution across the entire product line without warning."
    )
    changed, n_cur, n_pri = find_changed_paragraphs(_filing(shared + [new_risk]), _filing(shared))
    assert any("logistics provider" in c for c in changed)
    assert not any("Shared paragraph" in c for c in changed)
    assert n_cur == n_pri + 1


def test_result_is_capped():
    """A wholesale rewrite must not turn into one enormous prompt."""
    current = [
        f"Entirely distinct risk {i} concerning regulatory exposure in jurisdiction {i}, "
        f"with consequences unique to that market and no prior-year equivalent text."
        for i in range(80)
    ]
    # Must exceed the minimum section size, or the prior filing is correctly
    # rejected as too short to be a real section rather than a contents entry.
    prior = [f"{_RISK} Unrelated prior paragraph {i} of sufficient length here." for i in range(25)]
    changed, _, _ = find_changed_paragraphs(_filing(current), _filing(prior))
    assert len(changed) == MAX_PARAGRAPHS_TO_ASSESS
