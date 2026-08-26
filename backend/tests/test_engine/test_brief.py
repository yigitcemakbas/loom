"""Brief synthesis tests.

This module decides the one sentence a reader sees, so its failure modes are
product failures rather than cosmetic ones. Two are pinned hardest:

  - reporting an unanalysed company as a calm one
  - stating a confident verdict off a single finding

Both are lies of a kind a reader cannot detect from the screen, which is
exactly why they need tests rather than review.
"""

import uuid
from datetime import datetime, timedelta, timezone

from app.engine.brief import build_brief
from app.models.brief import Stance
from app.models.signal import Signal, SignalType

NOW = datetime(2026, 8, 26, tzinfo=timezone.utc)


def _sig(
    *,
    direction: str | None = "negative",
    magnitude: str = "moderate",
    priority: float = 0.8,
    days_ago: int = 1,
    summary: str = "Component costs: rising memory prices squeeze hardware margins.",
    doc_subtype: str = "10-Q",
    signal_type: SignalType = SignalType.NEW_RISK_FACTOR,
    confidence: float = 0.9,
) -> Signal:
    return Signal(
        id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        signal_type=signal_type,
        summary=summary,
        detail=summary,
        market_direction=direction,
        market_magnitude=magnitude,
        confidence=confidence,
        priority=priority,
        occurred_at=NOW - timedelta(days=days_ago),
        signal_metadata={"doc_subtype": doc_subtype},
    )


def _many(n: int, **kw) -> list[Signal]:
    """n findings that are deliberately distinct, so theme-dedupe leaves them alone."""
    kw.setdefault("summary_template", "Theme {i}: distinct concern number {i} about operations.")
    template = kw.pop("summary_template")
    return [_sig(summary=template.format(i=i), **kw) for i in range(n)]


# ---- the two dangerous failure modes ----------------------------------


def test_unassessed_findings_never_read_as_a_calm_company():
    """Signals analysed before market-impact assessment existed carry no
    direction. Treating those as 'neutral' reported companies with twenty open
    findings as having nothing notable, which is worse than showing nothing."""
    signals = _many(10, direction=None)

    brief = build_brief(signals, now=NOW)

    assert brief.stance == Stance.INSUFFICIENT
    assert brief.stance != Stance.QUIET
    assert "not been assessed" in brief.headline
    assert brief.evidence["unassessed"] == 10


def test_no_view_means_no_confidence():
    """A stance of 'no view offered' printed beside '83% confident' reads as a
    contradiction and undermines every other number on the page."""
    brief = build_brief(_many(10, direction=None), now=NOW)

    assert brief.confidence == 0.0


def test_a_single_finding_cannot_produce_a_verdict():
    brief = build_brief([_sig(magnitude="major", priority=0.95)], now=NOW)

    assert brief.stance == Stance.INSUFFICIENT


def test_strong_verdict_requires_more_than_one_kind_of_source():
    """Ten findings extracted from one filing are one opinion about one
    document, not ten independent confirmations."""
    one_source = build_brief(_many(6, magnitude="major", doc_subtype="10-Q"), now=NOW)

    assert one_source.stance == Stance.NEGATIVE  # softened from STRONG_NEGATIVE

    mixed_sources = _many(3, magnitude="major", doc_subtype="10-Q") + [
        _sig(magnitude="major", doc_subtype="earnings_call", summary="Call: management confirmed the same pressure."),
        _sig(magnitude="major", doc_subtype="news", summary="News: suppliers raised prices again."),
    ]
    assert build_brief(mixed_sources, now=NOW).stance == Stance.STRONG_NEGATIVE


# ---- stance direction --------------------------------------------------


def test_agreeing_negative_findings_lean_negative():
    brief = build_brief(
        _many(3, direction="negative") + [
            _sig(direction="negative", doc_subtype="earnings_call", summary="Call: costs rose."),
        ],
        now=NOW,
    )
    assert brief.stance in (Stance.NEGATIVE, Stance.STRONG_NEGATIVE)


def test_balanced_evidence_reads_as_mixed():
    signals = (
        _many(2, direction="negative")
        + [_sig(direction="positive", doc_subtype="earnings_call", summary=f"Upside {i}: demand grew strongly.") for i in range(2)]
    )
    brief = build_brief(signals, now=NOW)

    assert brief.stance == Stance.MIXED
    assert "both ways" in brief.headline


def test_assessed_but_neutral_findings_read_as_quiet():
    """Genuinely judged-neutral is a real answer, and distinct from unjudged."""
    signals = _many(6, direction="neutral") + [
        _sig(direction="neutral", doc_subtype="earnings_call", summary="Call: routine update.")
    ]
    brief = build_brief(signals, now=NOW)

    assert brief.stance == Stance.QUIET
    assert "routine" in brief.headline


def test_major_findings_outweigh_minor_ones():
    signals = [
        _sig(direction="negative", magnitude="major", doc_subtype="10-Q", summary="Alpha: a serious problem emerged."),
        _sig(direction="negative", magnitude="major", doc_subtype="earnings_call", summary="Beta: confirmed on the call."),
        _sig(direction="positive", magnitude="minor", doc_subtype="news", summary="Gamma: a small win."),
        _sig(direction="positive", magnitude="minor", doc_subtype="news", summary="Delta: another small win."),
    ]
    assert build_brief(signals, now=NOW).stance in (Stance.NEGATIVE, Stance.STRONG_NEGATIVE)


# ---- drivers -----------------------------------------------------------


def test_one_story_told_five_times_uses_one_driver_slot():
    """A theme repeated across a filing, a call, and three news items would
    otherwise fill every slot and hide everything else."""
    repeated = [
        _sig(summary="Memory costs: rising memory prices squeeze hardware margins badly.", doc_subtype=s)
        for s in ("10-Q", "earnings_call", "news", "news")
    ]
    distinct = [
        _sig(summary="Antitrust ruling: court remedies threaten search licensing revenue.", doc_subtype="10-K"),
        _sig(summary="Currency headwinds: exchange rates reduce reported overseas sales.", doc_subtype="10-K"),
    ]
    brief = build_brief(repeated + distinct, now=NOW)

    titles = [d.title.lower() for d in brief.drivers]
    assert len(titles) == len(set(titles))
    assert any("antitrust" in t for t in titles)


def test_driver_records_every_source_that_showed_the_same_story():
    repeated = [
        _sig(summary="Memory costs: rising memory prices squeeze hardware margins badly.", doc_subtype=s)
        for s in ("10-Q", "earnings_call", "news")
    ]
    brief = build_brief(repeated + _many(2), now=NOW)

    memory = next(d for d in brief.drivers if "memory" in d.title.lower())
    assert set(memory.sources) >= {"10-Q", "earnings_call", "news"}


def test_driver_detail_does_not_repeat_its_own_heading():
    brief = build_brief(
        _many(2) + [_sig(doc_subtype="news", summary="Tariffs: import duties raise landed component cost.")],
        now=NOW,
    )
    tariffs = next(d for d in brief.drivers if d.title == "Tariffs")

    assert not tariffs.detail.lower().startswith("tariffs:")


def test_long_summaries_are_cut_on_a_word_boundary():
    long = "A very long finding sentence without any colon that runs past the title limit and would otherwise be sliced mid-word"
    brief = build_brief(
        [_sig(summary=long), _sig(doc_subtype="news", summary="Other: something else happened here.")],
        now=NOW,
    )
    title = next(d.title for d in brief.drivers if d.title.startswith("A very long"))

    assert title.endswith("…")
    assert not title.rstrip("…").endswith(" ")


# ---- what changed ------------------------------------------------------


def test_what_changed_reports_only_findings_newer_than_the_last_read():
    old = _many(2, days_ago=40)
    new = [_sig(days_ago=1, doc_subtype="news", summary="Fresh: a brand new concern appeared.")]

    brief = build_brief(old + new, previous_generated_at=NOW - timedelta(days=10), now=NOW)

    assert brief.what_changed is not None
    assert "1 new finding" in brief.what_changed


def test_no_previous_read_means_nothing_to_diff_against():
    assert build_brief(_many(3), now=NOW).what_changed is None


# ---- windowing ---------------------------------------------------------


def test_findings_outside_the_window_are_excluded():
    assert build_brief(_many(5, days_ago=400), now=NOW).stance == Stance.INSUFFICIENT


def test_dismissed_findings_do_not_shape_the_verdict():
    signals = _many(4)
    for s in signals:
        s.dismissed_at = NOW

    assert build_brief(signals, now=NOW).stance == Stance.INSUFFICIENT


def test_empty_input_is_handled():
    brief = build_brief([], now=NOW)

    assert brief.stance == Stance.INSUFFICIENT
    assert brief.drivers == []
    assert brief.confidence == 0.0
