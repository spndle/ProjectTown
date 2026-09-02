import pytest

from backend.app.runtime import (
    BudgetExceeded,
    BudgetGuard,
    CapabilityRouter,
    Event,
    EventReducer,
    ExecutionBudget,
    ProgressWatchdog,
    SparseMessageBus,
    stable_hash,
)


def test_hash_and_nested_reducer_are_deterministic_and_pure():
    assert stable_hash({"b": 2, "a": 1}) == stable_hash({"a": 1, "b": 2})
    state = {"config": {"x": 1}, "items": [1]}
    event = Event("update", {"patch": {"config": {"y": 2}, "items": [3]}})
    out = EventReducer().apply(state, event)
    assert out == {"config": {"x": 1, "y": 2}, "items": [3]}
    assert state == {"config": {"x": 1}, "items": [1]}


def test_budget_covers_steps_tools_messages_and_time():
    now = [10.0]
    guard = BudgetGuard(
        ExecutionBudget(
            max_steps=1,
            max_tool_calls=1,
            max_messages=1,
            max_tokens=10,
            max_replans=1,
            max_seconds=5,
        ),
        clock=lambda: now[0],
    )
    guard.consume("step")
    guard.consume("tool_call")
    guard.consume("message")
    guard.consume_tokens(10)
    guard.consume_replan()
    assert guard.remaining("step") == 0
    with pytest.raises(BudgetExceeded):
        guard.consume("step")
    with pytest.raises(BudgetExceeded):
        guard.consume_tokens(1)
    now[0] = 16.0
    with pytest.raises(BudgetExceeded):
        guard.consume("message", 0)


def test_watchdog_only_triggers_without_new_evidence():
    wd = ProgressWatchdog(threshold=2)
    assert not wd.observe("a", "w", ["e1"]).triggered
    assert wd.observe("a", "w", ["e1"]).triggered
    assert not wd.observe("a", "w", ["e2"]).triggered
    assert not wd.observe("a", "w", ["e1"]).has_new_evidence


def test_router_and_sparse_bus():
    router = CapabilityRouter()
    assert router.route(1, "low") == ["agent"]
    assert router.route(3, "low") == ["agent"]
    assert len(router.route(4, "low")) == 3
    assert router.route(8, "high", force_multi=False) == ["agent"]
    bus = SparseMessageBus()
    first = bus.publish("t", 1, "a", "b", ["e1"], "ok", {"x": 1})
    duplicate = bus.publish("t", 2, "a", "b", ["e1"], "ok", {"x": 1})
    newer = bus.publish("t", 2, "a", "b", ["e1", "e2"], "ok", {"x": 1})
    assert first.accepted and not duplicate.accepted and newer.accepted
