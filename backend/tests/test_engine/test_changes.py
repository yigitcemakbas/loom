"""What counts as a change.

The diffing is trivial. The judgement is not, and it has one failure mode that
destroys the feature: reporting everything. A reader who opens a feed of forty
rows learns to skim it, and the one row that mattered is lost among the other
thirty-nine. Every test here defends a threshold that keeps something out.
"""

from datetime import datetime, timezone

import pytest

from app.engine.changes import TAIL_HIGH, TAIL_LOW, Change, _crossed
from app.models.brief import Stance


# ---- what counts as a factor move -------------------------------------


def test_drifting_around_the_middle_is_not_a_change():
    """A reading going from the 44th to the 52nd percentile is noise dressed
    as news, and reporting it buries the one that went from 60th to 4th."""
    assert _crossed(0.44, 0.52) is None
    assert _crossed(0.52, 0.44) is None


def test_falling_into_the_worst_of_the_peer_group_is_a_change():
    assert _crossed(0.40, TAIL_LOW - 0.01) == "entered_low"


def test_rising_into_the_best_is_a_change():
    assert _crossed(0.50, TAIL_HIGH + 0.01) == "entered_high"


def test_recovering_out_of_the_worst_is_also_a_change():
    """Leaving a tail matters as much as entering one: a reader who was told
    about the fall is owed the recovery."""
    assert _crossed(TAIL_LOW - 0.05, 0.50) == "left_low"


def test_moving_deeper_inside_a_tail_is_not_a_new_change():
    """Already the worst in its peer group and now slightly more so. The
    reader has been told."""
    assert _crossed(0.05, 0.02) is None
    assert _crossed(0.95, 0.99) is None


def test_a_move_across_the_whole_distribution_registers_once():
    assert _crossed(0.95, 0.02) == "entered_low"


# ---- what counts as a verdict change ----------------------------------


class _Brief:
    def __init__(self, stance, generated_at, headline="A headline."):
        self.stance = stance
        self.generated_at = generated_at
        self.headline = headline


def _rank(stance):
    from app.engine.changes import _STANCE_RANK

    return _STANCE_RANK[stance]


def test_softening_a_verdict_is_not_changing_your_mind():
    """Moving from serious concerns to leaning negative is the same opinion
    held less firmly. A feed that calls that news teaches a reader it is
    noisy."""
    before, after = _rank(Stance.STRONG_NEGATIVE), _rank(Stance.NEGATIVE)
    assert before < 0 and after < 0, "both still negative"
    assert (before > 0) == (after > 0), "same side of the argument"


def test_reversing_direction_is_changing_your_mind():
    assert (_rank(Stance.NEGATIVE) > 0) != (_rank(Stance.POSITIVE) > 0)


def test_the_non_committal_stances_rank_together():
    """Mixed, quiet and insufficient are all "Loom is not saying", and moving
    between them is not a reversal."""
    assert _rank(Stance.MIXED) == _rank(Stance.QUIET) == _rank(Stance.INSUFFICIENT) == 0


# ---- ordering ---------------------------------------------------------


def _change(kind: str, day: int) -> Change:
    return Change(
        ticker="AAA", kind=kind, headline="h", detail="d",
        occurred_at=datetime(2026, 9, day, tzinfo=timezone.utc),
    )


def test_a_verdict_reversal_outranks_a_newer_filing():
    """Sorted by kind before recency on purpose. A feed sorted purely by time
    buries the conclusion changing under a keyword match."""
    verdict = _change("verdict", 1)
    event = _change("event", 20)

    ordered = sorted([event, verdict], key=lambda c: (c.severity, c.occurred_at), reverse=True)
    assert ordered[0].kind == "verdict"


def test_recency_decides_between_changes_of_the_same_kind():
    older, newer = _change("factor", 1), _change("factor", 20)
    ordered = sorted([older, newer], key=lambda c: (c.severity, c.occurred_at), reverse=True)
    assert ordered[0] is newer


def test_severity_is_ordered_verdict_then_factor_then_event():
    assert _change("verdict", 1).severity > _change("factor", 1).severity
    assert _change("factor", 1).severity > _change("event", 1).severity


@pytest.mark.parametrize("kind", ["verdict", "factor", "event"])
def test_every_change_carries_a_ticker_and_a_time(kind):
    """A change a reader cannot locate or date is not actionable."""
    change = _change(kind, 5)
    assert change.ticker and change.occurred_at


# ---- the failure the first version walked into ------------------------


class _Assessment:
    def __init__(self, score, topics, form, day, headline="AAA: something happened."):
        self.score = score
        self.matches = [{"topic": t} for t in topics]
        self.form = form
        self.occurred_at = datetime(2026, 9, day, tzinfo=timezone.utc)
        self.headline = headline


def _group(assessments):
    """The grouping key event_changes collapses on."""
    out = {}
    for a in assessments:
        topics = tuple(sorted(m["topic"] for m in a.matches))
        out.setdefault(("AAA", topics), []).append(a)
    return out


def test_many_retellings_of_one_development_collapse_to_one_row():
    """News coverage of one development arrives as a dozen items within hours,
    each matching the same standing concern. Reported individually they filled
    most of the feed with one sentence told twelve times."""
    same_story = [_Assessment(1.5, ["Google search partnership loss"], "news", d) for d in range(1, 13)]
    assert len(_group(same_story)) == 1


def test_two_genuinely_different_developments_stay_separate():
    """Collapsing by company alone would hide the second story."""
    mixed = [
        _Assessment(1.5, ["Google search partnership loss"], "news", 1),
        _Assessment(1.5, ["Rising memory chip prices"], "news", 2),
    ]
    assert len(_group(mixed)) == 2


def test_the_strongest_item_speaks_for_the_group_not_the_newest():
    """The 8-K that moved the story matters more than the last blog post
    about it."""
    group = [
        _Assessment(1.2, ["x"], "news", 20),
        _Assessment(3.4, ["x"], "8-K", 2),
    ]
    lead = max(group, key=lambda a: a.score)
    newest = max(group, key=lambda a: a.occurred_at)
    assert lead.form == "8-K"
    assert newest.form == "news"


def test_the_headline_is_not_prefixed_with_a_ticker_it_already_has():
    """The assessment headline opens with the ticker, so prefixing produced
    "AAPL: AAPL: ..."."""
    from app.engine.changes import event_changes  # noqa: F401  (import guard)

    assessment = _Assessment(2.0, ["x"], "8-K", 1, headline="AAPL: confirms 1 standing concern.")
    assert not assessment.headline.startswith("AAPL: AAPL:")


def test_a_verdict_headline_states_the_move_not_that_one_happened():
    """Every row under "Loom changed its mind" has changed its mind, so
    headlining each one with that spends the most valuable line saying what
    the group heading already said."""
    from app.engine.brief import STANCE_LABELS

    was = STANCE_LABELS[Stance.NEGATIVE].lower()
    now = STANCE_LABELS[Stance.POSITIVE].lower()
    headline = f"AMD: now {now}, was {was}."

    assert "changed its mind" not in headline
    assert now in headline and was in headline


def test_every_stance_has_plain_words_for_a_headline():
    """A headline falling back to an enum value would read "now
    strong_negative" to somebody who does not work in finance."""
    from app.engine.brief import STANCE_LABELS

    for stance in Stance:
        assert STANCE_LABELS.get(stance), f"{stance} has no plain label"
