from __future__ import annotations

import json
from pathlib import Path

REPLAYS = Path("examples/replays")


def _load(name: str) -> dict:
    with (REPLAYS / name).open(encoding="utf-8") as handle:
        return json.load(handle)


def _event_types(replay: dict) -> list[str]:
    events = replay["events"]
    assert events == sorted(events, key=lambda event: event["sequence"])
    return [event["event_type"] for event in events]


def test_normal_replay_is_runtime_completed_with_receipts_and_evidence():
    replay = _load("normal.json")
    assert replay["generated_from_runtime"] is True
    assert replay["scenario"] == "normal"
    assert replay["final_status"] == "completed"
    assert {"ToolCommitted", "MilestoneVerified", "QuestCompleted"} <= set(
        _event_types(replay)
    )
    assert replay["receipts"]
    assert replay["evidence"]


def test_response_loss_replay_records_unknown_effect_and_reconciliation():
    replay = _load("recovery.json")
    assert replay["generated_from_runtime"] is True
    assert replay["scenario"] == "response-loss-recovery"
    assert replay["final_status"] == "completed"
    assert {
        "ToolEffectUnknown",
        "RecoveryStarted",
        "RecoveryCompleted",
        "QuestCompleted",
    } <= set(_event_types(replay))
    assert replay["recovery_evidence"] == {
        "fault_point": "after_effect_before_receipt",
        "unknown_effect_observed": True,
        "reconciled_receipt_observed": True,
    }


def test_loop_replay_is_watchdog_blocked_with_loop_detected():
    replay = _load("loop-blocked.json")
    assert replay["generated_from_runtime"] is True
    assert replay["scenario"] == "watchdog-loop-blocked"
    assert replay["final_status"] == "waiting_user"
    assert replay["final_error"]["code"] == "LOOP_DETECTED"
    assert "LoopDetected" in _event_types(replay)
    assert replay["progress_entries"]
