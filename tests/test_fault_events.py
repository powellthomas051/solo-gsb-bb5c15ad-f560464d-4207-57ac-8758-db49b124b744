"""Unit tests for fault-event grouping (services.build_fault_events)."""

from app.services import build_fault_events


def fault(fid, rid, name, start, end, logic="AND"):
    return {
        "id": fid,
        "rule_id": rid,
        "rule_name": name,
        "logic": logic,
        "start": start,
        "end": end,
        "duration_required": 0,
        "confirmed_at": start,
        "evidence": [],
    }


def member_ids(event):
    return [m["id"] for m in event["members"]]


def test_single_chain_from_different_rules_is_one_event():
    faults = [
        fault(1, 1, "r1", 0, 5),
        fault(2, 2, "r2", 5, 10),   # touches fault 1 at t=5
        fault(3, 3, "r3", 8, 14),   # overlaps fault 2; disjoint from fault 1
    ]
    events = build_fault_events(faults)
    assert len(events) == 1
    ev = events[0]
    assert member_ids(ev) == [1, 2, 3]
    assert (ev["start"], ev["end"]) == (0, 14)
    assert ev["fault_count"] == 3
    assert ev["rule_count"] == 3
    assert [r["rule_id"] for r in ev["rules"]] == [1, 2, 3]


def test_gap_keeps_events_apart():
    faults = [
        fault(1, 1, "r1", 0, 5),
        fault(2, 2, "r2", 6, 10),  # 1-second gap -> separate events
    ]
    events = build_fault_events(faults)
    assert len(events) == 2
    assert member_ids(events[0]) == [1]
    assert member_ids(events[1]) == [2]


def test_same_rule_faults_can_group_and_rules_deduplicate():
    faults = [
        fault(1, 1, "r1", 0, 5),
        fault(2, 1, "r1", 5, 8),  # same rule, touching -> still one event
        fault(3, 2, "r2", 7, 12),
    ]
    events = build_fault_events(faults)
    assert len(events) == 1
    assert member_ids(events[0]) == [1, 2, 3]
    assert events[0]["rule_count"] == 2
    assert [r["rule_id"] for r in events[0]["rules"]] == [1, 2]
    assert (events[0]["start"], events[0]["end"]) == (0, 12)


def test_envelope_takes_min_start_and_max_end():
    faults = [
        fault(1, 1, "r1", 2, 6),
        fault(2, 2, "r2", 0, 4),
        fault(3, 3, "r3", 5, 20),
    ]
    events = build_fault_events(faults)
    assert len(events) == 1
    assert (events[0]["start"], events[0]["end"]) == (0, 20)
    # members stay ordered by start (fault 2 starts first)
    assert member_ids(events[0]) == [2, 1, 3]


def test_multiple_independent_events_are_span_sorted():
    faults = [
        fault(1, 1, "r1", 100, 110),
        fault(2, 2, "r2", 0, 10),
        fault(3, 3, "r3", 50, 60),
    ]
    events = build_fault_events(faults)
    assert [(e["start"], e["end"]) for e in events] == [(0, 10), (50, 60), (100, 110)]
