"""The boilerplate baseline, and the guarantee that it never loses a decisive line.

`engine/disclosure.py` measures what documents of a genre normally say from
findings the model extracted, so the expectation and the extractor share a bias
and agree about it by construction. This module measures the same idea from raw
filing text, which makes the baseline independent of the instrument.

The risk in doing that is obvious and is what most of these tests are about: a
risk written entirely in the industry's vocabulary can be the decisive fact for
one filer the year it first appears. "Our largest customer may not renew" is in
hundreds of 10-Ks and is the story of exactly one of them. So corpus rarity is a
feature and never a filter, and where it disagrees with novelty against the
company's own prior filing, novelty wins.
"""

import math

from app.engine.corpus import (
    MIN_COMPANIES_TO_STORE,
    RAREST_TERMS_CONSIDERED,
    Vocabulary,
    terms_of,
)
from app.engine.diffing import (
    MAX_PARAGRAPHS_TO_ASSESS,
    SIMILARITY_THRESHOLD,
    WEIGHT_COMPANY_NOVELTY,
    WEIGHT_CORPUS_RARITY,
    _unmatched,
    rank_score,
)

_BOILERPLATE = (
    "We face intense competition across every market in which we operate and "
    "many competitors command greater financial resources than we do."
)
_SPECIFIC = (
    "The Irish Revenue Commissioners assessed additional tax against our Cork "
    "subsidiary following the State Aid decision."
)


def _vocabulary(companies: int = 400, **frequency) -> Vocabulary:
    """A vocabulary where the named terms are as common as stated."""
    return Vocabulary(document_frequency=dict(frequency), companies=companies)


# ---- the guarantee -----------------------------------------------------------


def test_novelty_to_the_company_outweighs_rarity_against_the_corpus():
    """The safeguard, stated as arithmetic rather than as an intention.

    A paragraph that has never appeared in this filer's own prior filing must
    outrank one full of unusual words that the company has been carrying for
    years — because the first is news about the company and the second is news
    about the dictionary.
    """
    brand_new_but_boilerplate = rank_score(similarity=0.0, rarity=0.0)
    barely_new_but_very_rare = rank_score(
        similarity=SIMILARITY_THRESHOLD - 0.01, rarity=1.0
    )

    assert brand_new_but_boilerplate > barely_new_but_very_rare
    assert WEIGHT_COMPANY_NOVELTY > WEIGHT_CORPUS_RARITY


def test_rarity_cannot_remove_a_paragraph_that_would_otherwise_survive():
    """The whole design in one assertion: supplying a vocabulary changes the
    order and never the count.

    The cap existed before this module did. What the baseline changes is which of
    the over-cap paragraphs is the one lost, and it changes it in favour of the
    more distinctive — so it reduces the risk of losing something decisive rather
    than introducing it.
    """
    source = [
        f"Risk number {i}: the Company depends on arrangement {i} whose failure "
        f"could materially harm its results in that market."
        for i in range(MAX_PARAGRAPHS_TO_ASSESS + 20)
    ]
    against = ["Something else entirely, bearing no resemblance to the above."]
    vocabulary = _vocabulary(competition=390, intense=380)

    without = _unmatched(source, against)
    with_rarity = _unmatched(source, against, vocabulary.rarity)

    assert len(with_rarity) == len(without) == MAX_PARAGRAPHS_TO_ASSESS
    assert set(with_rarity) <= set(source)


def test_a_paragraph_new_to_the_company_is_never_ranked_below_an_unchanged_one():
    """An unchanged paragraph is excluded before rarity is consulted at all, so
    no amount of rare vocabulary can promote one."""
    unchanged = _BOILERPLATE
    novel = "A newly disclosed dependency on a single logistics provider in Asia."
    vocabulary = _vocabulary(
        irish=2, commissioners=3, logistics=200, provider=300, asia=250
    )

    kept = _unmatched([unchanged, novel], [unchanged], vocabulary.rarity)

    assert novel in kept
    assert unchanged not in kept


# ---- what the measure actually measures -------------------------------------


# Every content word of both sentences, so neither is scored as rare merely for
# being absent from a sparse fixture. An earlier version of this test omitted
# words like "operate" and "command", which then scored 1.0 as unseen terms and
# made the boilerplate sentence look as distinctive as the specific one.
_FIXTURE_FREQUENCY = dict(
    # the industry's vocabulary: nearly every filer uses these
    face=380, intense=360, competition=390, across=395, every=398, market=396,
    operate=385, many=397, competitors=380, command=300, greater=370,
    financial=399, resources=392,
    # ordinary words that happen to sit in the specific sentence
    revenue=395, additional=396, against=390, following=380, state=370,
    decision=360, subsidiary=210, assessed=180,
    # the particulars
    irish=4, commissioners=3, cork=2,
)


def test_industry_vocabulary_scores_lower_than_a_named_particular():
    """The point of the module. Measured on the real 442-company corpus these came
    out at 0.032 for the generic sentence and 0.402 for the specific one."""
    vocabulary = _vocabulary(**_FIXTURE_FREQUENCY)

    assert vocabulary.rarity(_SPECIFIC) > vocabulary.rarity(_BOILERPLATE)
    assert vocabulary.rarity(_BOILERPLATE) < 0.2


def test_a_term_the_table_has_never_seen_is_treated_as_rarest():
    """Absence is the signal. Terms below MIN_COMPANIES_TO_STORE are deliberately
    not stored — there are hundreds of thousands of them — so a missing term must
    read as maximally unusual rather than as unknown."""
    vocabulary = _vocabulary(competition=390)

    assert vocabulary.term_rarity("competition") < 0.2
    assert vocabulary.term_rarity("nonesuchtermanywhere") == 1.0
    assert MIN_COMPANIES_TO_STORE >= 2


def test_only_the_rarest_terms_decide_the_score():
    """A paragraph is specific because of a few distinctive words set in ordinary
    prose. Averaging over the ordinary prose washes exactly those out, so the
    score reads the rarest few instead."""
    # Alphabetic, because `terms_of` matches letters only: "common0" tokenises to
    # "common", so a numbered fixture collapses to one term and scores as unseen.
    filler_words = [
        "alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel",
        "india", "juliet", "kilo", "lima", "mike", "november", "oscar", "papa",
        "quebec", "romeo", "sierra", "tango", "uniform", "victor", "whisky",
        "xray", "yankee", "zulu", "anchor", "beacon", "cedar", "dune",
    ]
    vocabulary = _vocabulary(**{w: 400 for w in filler_words}, cork=2)

    filler = " ".join(filler_words)
    assert vocabulary.rarity(filler) == 0.0
    assert vocabulary.rarity(f"{filler} cork") > vocabulary.rarity(filler)
    assert RAREST_TERMS_CONSIDERED <= 10


def test_an_unmeasurable_paragraph_returns_none_rather_than_zero():
    """None is not boilerplate. A thin corpus or a paragraph with no scorable
    terms has not been measured, and a caller treating that as "ordinary" must do
    it knowingly — the convention the whole codebase keeps."""
    thin = Vocabulary(document_frequency={"competition": 2}, companies=3)

    assert thin.rarity(_BOILERPLATE) is None
    assert not thin.measured
    assert _vocabulary(competition=390).rarity("the and for") is None


def test_rank_score_ignores_an_unmeasured_rarity_instead_of_penalising_it():
    assert rank_score(0.2, None) == rank_score(0.2, 0.0)


# ---- tokenisation ----------------------------------------------------------


def test_terms_are_deduplicated_within_a_filing():
    """Document frequency asks whether a company uses a term at all. Repetition
    inside one filing is style, not evidence about the term's commonness."""
    assert terms_of("supply supply supply chain") == {"supply", "chain"}


def test_the_reasons_for_a_score_can_be_shown():
    """A number a reader cannot interrogate is an assertion."""
    vocabulary = _vocabulary(
        competition=390, irish=3, commissioners=2, cork=2, subsidiary=200
    )

    unusual = vocabulary.unusual_terms(_SPECIFIC, limit=3)

    assert unusual
    assert "competition" not in unusual


# ---- the length gate -------------------------------------------------------


def test_the_length_gate_is_derived_from_the_threshold_not_equal_to_it():
    """`ratio()` is 2M/T and M cannot exceed the shorter sequence, so the best
    achievable similarity for two paragraphs is 2·min/(min+max). Solving that
    against the threshold gives min/max >= threshold/(2-threshold).

    Gating on the threshold itself was stricter than the arithmetic allows.
    """
    from app.engine.diffing import MIN_LENGTH_RATIO, SIMILARITY_THRESHOLD

    assert MIN_LENGTH_RATIO == SIMILARITY_THRESHOLD / (2 - SIMILARITY_THRESHOLD)
    assert MIN_LENGTH_RATIO < SIMILARITY_THRESHOLD


def test_a_paragraph_carried_over_and_expanded_is_not_reported_as_new():
    """The defect the faithfulness harness found, as a regression test.

    A risk the company kept and enlarged sits around a 0.69 length ratio and a
    0.82 similarity. The old gate skipped the comparison entirely, so
    `best_similarity` returned 0.0 and the paragraph was emitted as a new risk
    factor with an 82%-similar predecessor sitting in the prior filing. This was
    21 of 71 checkable risk-diff findings.
    """
    from app.engine.diffing import SIMILARITY_THRESHOLD, _unmatched, best_similarity

    prior = (
        "We face intense competition across every market in which we operate and "
        "many competitors command greater financial resources than we do, which "
        "may affect pricing."
    )
    expanded = prior + " In addition, new entrants have recently increased that pressure further."

    assert len(prior) / len(expanded) < SIMILARITY_THRESHOLD, "fixture must sit inside the old gate"
    assert best_similarity(expanded, [prior]) >= SIMILARITY_THRESHOLD
    assert _unmatched([expanded], [prior]) == []


def test_a_genuinely_unrelated_paragraph_is_still_reported():
    """The fix widens what gets compared; it must not stop anything being new."""
    from app.engine.diffing import _unmatched

    prior = "We face intense competition across every market in which we operate."
    novel = (
        "The Irish Revenue Commissioners assessed additional tax against our Cork "
        "subsidiary following the State Aid decision, and we have appealed."
    )

    assert _unmatched([novel], [prior]) == [novel]


# ---- page furniture --------------------------------------------------------


def test_the_running_table_of_contents_link_is_not_glued_onto_a_paragraph():
    """Why page furniture is a correctness problem and not cosmetic.

    `split_paragraphs` rejoins lines until sentence-ending punctuation, and the
    repeated "Table of Contents" link has none, so it attaches to whichever
    paragraph follows it. A page break falling in one year's filing and not the
    next then stops two identical risk factors from matching, and the
    carried-over paragraph is reported as new. Measured at 4.2% of risk-factor
    paragraphs before this was stripped.
    """
    from app.engine.sections import _clean

    assert _clean(["Table of Contents", "TABLE OF CONTENTS 12", "  table of contents  "]) == []
    assert _clean(["Table of Contents of our material agreements is below."]) == [
        "Table of Contents of our material agreements is below."
    ]


def test_an_identical_risk_factor_matches_across_a_page_break():
    """The observed failure, end to end: the same paragraph in two filings, one
    copy preceded by the page-break link, must still score as unchanged."""
    from app.engine.diffing import SIMILARITY_THRESHOLD, best_similarity
    from app.engine.sections import _clean, split_paragraphs

    body = (
        "The terms of our agreements with Kioxia with respect to Flash Ventures "
        "require that substantially all of our flash-based memory be obtained from "
        "Flash Ventures, which limits our ability to respond to market demand and "
        "supply changes and makes our results less predictable."
    )
    current = split_paragraphs("\n".join(_clean(body.split(". "))))
    prior = split_paragraphs("\n".join(_clean(["Table of Contents", *body.split(". ")])))

    assert current and prior
    assert best_similarity(current[0], prior) >= SIMILARITY_THRESHOLD
