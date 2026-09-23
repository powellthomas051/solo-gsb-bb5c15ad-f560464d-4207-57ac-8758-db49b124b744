"""Unit tests for the half-open interval / sweep-line engine.

All timestamps/durations are expressed in seconds (epoch semantics).
"""

import math

from app.engine import (
    Condition,
    Event,
    Interval,
    channel_held_intervals,
    connected_components,
    evaluate_rule,
    intersect,
    to_epoch,
    union,
)


def ev(eid, ts, value):
    return Event(id=eid, device="d", metric="m", ts=ts, value=value)


def minute_series(eid_base, predicate_fn):
    """One sample per minute for minutes 0..30, value from predicate_fn(minute)."""
    return [
        ev(eid_base + m, m * 60, predicate_fn(m))
        for m in range(31)
    ]


def ivs(rule_faults):
    return [(f.start, f.end) for f in rule_faults]


def test_union_merges_touching_half_open_intervals():
    merged = union([Interval(0, 6), Interval(6, 10), Interval(20, 25)])
    assert merged == [Interval(0, 10), Interval(20, 25)]


def test_union_does_not_bridge_real_gaps():
    merged = union([Interval(0, 6), Interval(6.5, 10)])
    assert merged == [Interval(0, 6), Interval(6.5, 10)]


def test_intersect_basic_and_touching():
    assert intersect([[Interval(0, 10)], [Interval(5, 15)]]) == [Interval(5, 10)]
    # [0,6) ∩ [6,10) is empty — half-open intervals do not overlap
    assert intersect([[Interval(0, 6)], [Interval(6, 10)]]) == []
    # touching transitions at the same instant on different operands stay continuous
    assert intersect(
        [[Interval(0, 6), Interval(6, 12)], [Interval(0, 12)]]
    ) == [Interval(0, 12)]
    # genuine interruption on one operand yields two separate pieces
    assert intersect(
        [[Interval(0, 6), Interval(6, 12)], [Interval(0, 5), Interval(7, 12)]]
    ) == [Interval(0, 5), Interval(7, 12)]


def test_timestamp_parsing():
    assert to_epoch(100) == 100.0
    assert to_epoch("2026-09-23T00:01:00Z") == to_epoch("2026-09-23T00:00:00Z") + 60
    assert to_epoch("100") == 100.0


def test_channel_held_intervals_last_sample_to_horizon():
    events = [ev(1, 0, 90), ev(2, 60, 10), ev(3, 120, 90)]
    cond = Condition("d", "m", ">", 50)
    held, witnesses = channel_held_intervals(events, cond, horizon=300)
    assert held == [Interval(0, 60), Interval(120, 300)]
    assert [w.id for w in witnesses] == [1, 3]


def test_channel_same_timestamp_last_wins():
    events = [ev(1, 0, 90), ev(2, 0, 10)]
    cond = Condition("d", "m", ">", 50)
    held, _ = channel_held_intervals(events, cond, horizon=10)
    assert held == []


def test_duration_filters_short_spike():
    # >=80 for 11 minutes [6,17); an isolated 1-minute spike at t=20
    events = minute_series(100, lambda m: 90 if (6 <= m <= 16 or m == 20) else 10)
    cond = Condition("d", "m", ">=", 80)
    faults = evaluate_rule(1, "AND", 300, [cond], {("d", "m"): events}, horizon=30 * 60)
    assert ivs(faults) == [(6 * 60, 17 * 60)]
    assert faults[0].confirmed_at == 6 * 60 + 300
    evidence = faults[0].evidence[0]
    assert [w.ts for w in evidence.events] == [m * 60 for m in range(6, 17)]
    assert evidence.held_intervals == [Interval(6 * 60, 17 * 60)]


def test_and_rule_intersection_with_duration():
    temp = minute_series(100, lambda m: 90 if 6 <= m <= 16 else 60)
    vib = minute_series(200, lambda m: 3.6 if 12 <= m <= 13 else 1.0)
    faults = evaluate_rule(
        2,
        "AND",
        120,
        [Condition("d", "temp", ">=", 80), Condition("d", "vib", ">", 3)],
        {("d", "temp"): temp, ("d", "vib"): vib},
        horizon=30 * 60,
    )
    assert ivs(faults) == [(12 * 60, 14 * 60)]


def test_or_touching_intervals_merge_across_channels():
    # vib high on [3,6); press low on touching [6,9) and separate [10,14)
    vib = minute_series(300, lambda m: 6.0 if 3 <= m <= 5 else 2.0)
    press = minute_series(400, lambda m: 26.0 if (6 <= m <= 8 or 10 <= m <= 13) else 52.0)
    faults = evaluate_rule(
        3,
        "OR",
        200,
        [Condition("d", "vib", ">", 5), Condition("d", "press", "<", 30)],
        {("d", "vib"): vib, ("d", "press"): press},
        horizon=30 * 60,
    )
    assert ivs(faults) == [(3 * 60, 9 * 60), (10 * 60, 14 * 60)]
    assert math.isclose(faults[0].end - faults[0].start, 6 * 60)
    groups = {g.condition.metric: g for g in faults[0].evidence}
    assert [w.ts for w in groups["vib"].events] == [3 * 60, 4 * 60, 5 * 60]
    assert [w.ts for w in groups["press"].events] == [6 * 60, 7 * 60, 8 * 60]
    assert groups["vib"].held_intervals == [Interval(3 * 60, 6 * 60)]
    assert groups["press"].held_intervals == [Interval(6 * 60, 9 * 60)]
    # second fault's evidence references only the second press dip
    second = {g.condition.metric: g for g in faults[1].evidence}
    assert [w.ts for w in second["press"].events] == [10 * 60, 11 * 60, 12 * 60, 13 * 60]
    assert second["vib"].events == []


def test_or_separate_short_windows_filtered_by_duration():
    vib = minute_series(500, lambda m: 6.0 if 3 <= m <= 6 else 2.0)      # [3,7): 4 min
    press = minute_series(600, lambda m: 26.0 if 10 <= m <= 11 else 52.0)  # [10,12): 2 min
    faults = evaluate_rule(
        4,
        "OR",
        200,
        [Condition("d", "vib", ">", 5), Condition("d", "press", "<", 30)],
        {("d", "vib"): vib, ("d", "press"): press},
        horizon=30 * 60,
    )
    # [3,7) is 4 min >= 200s -> fault; [10,12) is 2 min -> filtered
    assert ivs(faults) == [(3 * 60, 7 * 60)]


def test_and_missing_channel_never_matches():
    events = {("d", "temp"): [ev(1, 0, 90)]}
    faults = evaluate_rule(
        5,
        "AND",
        0,
        [Condition("d", "temp", ">", 50), Condition("d", "missing", ">", 0)],
        events,
        horizon=10,
    )
    assert faults == []


def _cc(starts_ends):
    # each item is (tag, start, end); component result -> list of tags
    items = [(tag, s, e) for tag, s, e in starts_ends]
    groups = connected_components(items, start=lambda it: it[1], end=lambda it: it[2])
    return [[it[0] for it in g] for g in groups]


def test_connected_components_overlap_and_touch():
    # [0,5) touches [5,10), [10,20) overlaps [5,10) -> one component
    assert _cc([("a", 0, 5), ("b", 5, 10), ("c", 10, 20)]) == [["a", "b", "c"]]
    # overlapping (non-touching) relation works too
    assert _cc([("a", 0, 8), ("b", 4, 12)]) == [["a", "b"]]


def test_connected_components_transitive_chain():
    # A and C never overlap; the chain A~B~C must still merge all three
    assert _cc([("a", 0, 5), ("b", 4, 9), ("c", 8, 14)]) == [["a", "b", "c"]]


def test_connected_components_real_gap_separates():
    # [0,5) and [5.5,9) do not touch; events stay separate and span-sorted
    assert _cc([("a", 0, 5), ("b", 5.5, 9), ("c", 20, 30)]) == [["a"], ["b"], ["c"]]
    # components are emitted in start order regardless of input order
    assert _cc([("c", 20, 30), ("a", 0, 5), ("b", 5.5, 9)]) == [["a"], ["b"], ["c"]]
