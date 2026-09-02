from __future__ import annotations

import sqlite3
import threading
from hashlib import sha256

import pytest

from backend.app.v1.storage import V1Storage


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _draft(storage: V1Storage, quest_id: str = "q1", max_tokens: int = 100) -> dict:
    return storage.create_draft(
        quest_id,
        {
            "id": f"contract-{quest_id}",
            "version": 1,
            "goal": "model audit",
            "budget": {"max_tokens": max_tokens},
        },
        {"id": f"plan-{quest_id}", "version": 1, "milestones": [{"id": "one"}]},
    )


def _prepare(storage: V1Storage, call_id: str = "call-1", reserve: int = 30) -> dict:
    state = storage.require_quest("q1")
    return storage.prepare_model_call(
        call_id,
        "q1",
        "plan",
        f"idem-{call_id}",
        _hash(f"input-{call_id}"),
        "prompt-v1",
        1,
        1,
        "deterministic",
        "public-model",
        {"temperature": 0},
        reserve,
        expected_state_version=state["state_version"],
        dispatch_token=f"dispatch-{call_id}",
    )


def test_model_call_success_is_idempotent_and_current(tmp_path) -> None:
    storage = V1Storage(tmp_path / "model.db")
    _draft(storage)
    before = (
        len(storage.list_events("q1")),
        len(storage._conn.execute("SELECT * FROM v1_goal_contracts").fetchall()),
        len(storage._conn.execute("SELECT * FROM v1_plan_versions").fetchall()),
    )
    prepared = _prepare(storage)
    attempt_id = prepared["attempts"][0]["attempt_id"]
    storage.mark_model_attempt_dispatched(attempt_id)
    first = storage.record_model_success(
        attempt_id,
        "q1",
        _hash("input-call-1"),
        _hash("response-one"),
        {"summary": "safe"},
        input_tokens=10,
        output_tokens=11,
        usage={
            "provider": "reported",
            "input_tokens": 10,
            "output_tokens": 11,
            "total_tokens": 21,
        },
        cost={"currency": "USD", "amount": "0.01"},
    )
    duplicate = storage.record_model_success(
        attempt_id,
        "q1",
        _hash("input-call-1"),
        _hash("response-one"),
        {"summary": "ignored"},
        input_tokens=0,
        output_tokens=0,
    )
    assert first["validation_status"] == "validated_current"
    assert first["usage"]["total_tokens"] == 21
    assert duplicate["settled_tokens"] == 21
    with pytest.raises(ValueError, match="response conflict"):
        storage.record_model_success(
            attempt_id,
            "q1",
            _hash("input-call-1"),
            _hash("different"),
            {"summary": "no"},
            input_tokens=0,
            output_tokens=0,
        )
    assert storage.model_token_usage("q1") == {
        "settled_tokens": 21,
        "held_tokens": 0,
        "max_tokens": 100,
        "available_tokens": 79,
    }
    assert (
        len(storage.list_events("q1")),
        len(storage._conn.execute("SELECT * FROM v1_goal_contracts").fetchall()),
        len(storage._conn.execute("SELECT * FROM v1_plan_versions").fetchall()),
    ) == before


@pytest.mark.parametrize("change", ["state", "contract", "plan", "input"])
def test_model_success_is_stale_when_any_binding_changes(tmp_path, change: str) -> None:
    storage = V1Storage(tmp_path / f"{change}.db")
    state = _draft(storage)
    prepared = _prepare(storage)
    attempt_id = prepared["attempts"][0]["attempt_id"]
    storage.mark_model_attempt_dispatched(attempt_id)
    if change == "state":
        storage.append_event(
            "q1", "Progressed", {"marker": "new"}, state["state_version"]
        )
    elif change == "contract":
        storage.append_event(
            "q1",
            "ContractChanged",
            {"contract": {**state["contract"], "goal": "changed"}},
            state["state_version"],
        )
    elif change == "plan":
        replacement = {"id": "plan-q1", "version": 2, "milestones": [{"id": "one"}]}
        with storage._transaction():
            storage._insert_plan_locked(
                "q1", replacement, storage._validate_plan(replacement), "now"
            )
        storage.append_event(
            "q1",
            "PlanChanged",
            {"plan_id": "plan-q1", "plan_version": 2},
            state["state_version"],
        )
    else:
        # Input is immutable per idempotency key; a duplicate request with a
        # different input hash is rejected before any new attempt is inserted.
        with pytest.raises(ValueError, match="idempotency conflict"):
            storage.prepare_model_call(
                "call-other",
                "q1",
                "plan",
                "idem-call-1",
                _hash("changed-input"),
                "prompt-v1",
                1,
                1,
                "deterministic",
                "public-model",
                {},
                1,
            )
    if change != "input":
        with pytest.raises(ValueError, match="idempotency conflict"):
            _prepare(storage)
    result = storage.record_model_success(
        attempt_id,
        "q1",
        _hash("input-call-1"),
        _hash(f"response-{change}"),
        {"ok": True},
        input_tokens=1,
        output_tokens=1,
    )
    if change == "input":
        assert result["validation_status"] == "validated_current"
    else:
        assert result["validation_status"] == "stale"


def test_reservation_concurrency_retry_unknown_and_recovery(tmp_path) -> None:
    path = tmp_path / "concurrency.db"
    storage = V1Storage(path)
    _draft(storage, max_tokens=50)
    barrier = threading.Barrier(2)
    results: list[str] = []

    def reserve(call_id: str) -> None:
        barrier.wait()
        try:
            _prepare(storage, call_id, 30)
            results.append("accepted")
        except ValueError:
            results.append("rejected")

    threads = [
        threading.Thread(target=reserve, args=(value,))
        for value in ("call-a", "call-b")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == ["accepted", "rejected"]
    call = storage.list_model_calls("q1")[0]
    attempt = call["attempts"][0]
    storage.mark_model_attempt_dispatched(attempt["attempt_id"])
    storage.mark_model_attempt_unknown(attempt["attempt_id"], {"code": "timeout"})
    assert storage.model_token_usage("q1")["held_tokens"] == 30
    with pytest.raises(ValueError, match="budget exceeded"):
        _prepare(storage, "call-too-much", 21)
    storage.close()
    reopened = V1Storage(path)
    assert reopened.recover_model_calls() == {
        "cancelled_prepared": 0,
        "unknown_dispatched": 0,
    }
    assert (
        reopened.get_model_call(call["call"]["call_id"])["attempts"][0]["status"]
        == "unknown_outcome"
    )
    reopened.close()


def test_prepared_and_dispatched_recovery_and_overage(tmp_path) -> None:
    storage = V1Storage(tmp_path / "recover.db")
    _draft(storage, max_tokens=40)
    _prepare(storage, "prepared", 10)
    dispatched = _prepare(storage, "dispatched", 10)
    dispatched_id = dispatched["attempts"][0]["attempt_id"]
    storage.mark_model_attempt_dispatched(dispatched_id)
    assert storage.recover_model_calls() == {
        "cancelled_prepared": 1,
        "unknown_dispatched": 1,
    }
    assert (
        storage.get_model_call("prepared")["attempts"][0]["validation_status"]
        == "cancelled_before_dispatch"
    )
    assert (
        storage.get_model_call("dispatched")["attempts"][0]["status"]
        == "unknown_outcome"
    )
    retried = storage.retry_model_call(
        "prepared",
        "prepared:2",
        "retry-prepared",
        "deterministic",
        "public-model",
        {},
        5,
    )
    storage.mark_model_attempt_dispatched(retried["attempt_id"])
    storage.record_model_failure(
        retried["attempt_id"], {"code": "known"}, input_tokens=45, output_tokens=0
    )
    assert storage.model_token_usage("q1")["settled_tokens"] == 45
    with pytest.raises(ValueError, match="budget exceeded"):
        _prepare(storage, "blocked", 1)


@pytest.mark.parametrize(
    "field,value",
    [
        ("call_id", "different-call"),
        ("request_schema_version", 2),
        ("candidate_schema_version", 2),
        ("prompt_version", "prompt-v2"),
        ("adapter", "other"),
        ("model", "other-model"),
        ("parameters", {"temperature": 1}),
        ("reserved_tokens", 31),
        ("dispatch_token", "different-dispatch"),
    ],
)
def test_prepare_idempotency_compares_full_request(
    tmp_path, field: str, value: object
) -> None:
    storage = V1Storage(tmp_path / f"{field}.db")
    state = _draft(storage)
    _prepare(storage)
    request: dict[str, object] = {
        "call_id": "call-1",
        "quest_id": "q1",
        "purpose": "plan",
        "idempotency_key": "idem-call-1",
        "input_hash": _hash("input-call-1"),
        "prompt_version": "prompt-v1",
        "request_schema_version": 1,
        "candidate_schema_version": 1,
        "adapter": "deterministic",
        "model": "public-model",
        "parameters": {"temperature": 0},
        "reserved_tokens": 30,
        "expected_state_version": state["state_version"],
        "dispatch_token": "dispatch-call-1",
    }
    request[field] = value
    with pytest.raises(ValueError, match="idempotency conflict"):
        storage.prepare_model_call(**request)


def test_callback_bindings_success_transition_and_immutable_audit_fields(
    tmp_path,
) -> None:
    storage = V1Storage(tmp_path / "callback.db")
    _draft(storage)
    prepared = _prepare(storage)
    attempt_id = prepared["attempts"][0]["attempt_id"]
    with pytest.raises(ValueError, match="already final"):
        storage.record_model_success(
            attempt_id,
            "q1",
            _hash("input-call-1"),
            _hash("response"),
            {"ok": True},
            input_tokens=1,
            output_tokens=1,
        )
    storage.mark_model_attempt_dispatched(attempt_id)
    with pytest.raises(ValueError, match="quest binding"):
        storage.record_model_success(
            attempt_id,
            "other",
            _hash("input-call-1"),
            _hash("response"),
            {"ok": True},
            input_tokens=1,
            output_tokens=1,
        )
    with pytest.raises(ValueError, match="input binding"):
        storage.record_model_success(
            attempt_id,
            "q1",
            _hash("other-input"),
            _hash("response"),
            {"ok": True},
            input_tokens=1,
            output_tokens=1,
        )
    assert storage.get_model_call("call-1")["attempts"][0]["settled_tokens"] == 0
    success = storage.record_model_success(
        attempt_id,
        "q1",
        _hash("input-call-1"),
        _hash("response"),
        {"ok": True},
        input_tokens=1,
        output_tokens=1,
    )
    assert (
        storage.get_model_call("call-1")["call"]["winning_attempt_id"]
        == success["attempt_id"]
    )
    with pytest.raises(sqlite3.DatabaseError, match="binding is immutable"):
        storage._conn.execute(
            "UPDATE v1_model_calls SET prompt_version='tampered' WHERE call_id='call-1'"
        )
    with pytest.raises(sqlite3.DatabaseError, match="binding is immutable"):
        storage._conn.execute(
            "UPDATE v1_model_attempts SET reserved_tokens=99 WHERE attempt_id=?",
            (attempt_id,),
        )
    with pytest.raises(sqlite3.DatabaseError, match="are immutable"):
        storage._conn.execute(
            "DELETE FROM v1_model_attempts WHERE attempt_id=?", (attempt_id,)
        )
    with pytest.raises(sqlite3.DatabaseError, match="are immutable"):
        storage._conn.execute("DELETE FROM v1_model_calls WHERE call_id='call-1'")
    with pytest.raises(ValueError, match="terminal"):
        storage.retry_model_call(
            "call-1", "call-1:2", "retry", "deterministic", "public-model", {}, 1
        )


@pytest.mark.parametrize(
    "secret_key",
    [
        "api_key",
        "credentials",
        "credential",
        "authorization",
        "password",
        "secret",
        "token",
    ],
)
def test_candidate_secret_is_rejected_before_persistence(
    tmp_path, secret_key: str
) -> None:
    storage = V1Storage(tmp_path / "candidate-secret.db")
    _draft(storage)
    prepared = _prepare(storage, "candidate-secret", 1)
    attempt_id = prepared["attempts"][0]["attempt_id"]
    storage.mark_model_attempt_dispatched(attempt_id)
    canary = "SECRET_MUST_NOT_PERSIST"
    with pytest.raises(ValueError, match="secrets"):
        storage.record_model_success(
            attempt_id,
            "q1",
            _hash("input-candidate-secret"),
            _hash("candidate-secret"),
            {"steps": [{"tool_args": {secret_key: canary}}]},
            input_tokens=1,
            output_tokens=1,
        )
    assert (
        storage._conn.execute(
            "SELECT COUNT(*) FROM v1_model_attempts WHERE candidate_json LIKE ?",
            (f"%{canary}%",),
        ).fetchone()[0]
        == 0
    )
    assert (
        storage.get_model_call("candidate-secret")["attempts"][0]["candidate"] is None
    )


def test_nested_secrets_summary_bounds_retry_cap_and_winner_recovery(tmp_path) -> None:
    storage = V1Storage(tmp_path / "limits.db")
    _draft(storage, max_tokens=1_000)
    with pytest.raises(ValueError, match="secrets"):
        storage.prepare_model_call(
            "bad",
            "q1",
            "plan",
            "bad",
            _hash("bad"),
            "v1",
            1,
            1,
            "a",
            "m",
            {"nested": {"access_token": "no"}},
            1,
        )
    _prepare(storage, "retry", 1)
    for attempt_id, token in (
        ("retry:1", "first"),
        ("retry:2", "second"),
        ("retry:3", "third"),
    ):
        storage.record_model_failure(
            attempt_id,
            {"code": "known"},
            usage={"input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
        )
        if attempt_id != "retry:3":
            storage.retry_model_call(
                "retry",
                f"retry:{int(attempt_id[-1]) + 1}",
                token,
                "deterministic",
                "public-model",
                {},
                1,
            )
    with pytest.raises(ValueError, match="retry limit"):
        storage.retry_model_call(
            "retry", "retry:4", "fourth", "deterministic", "public-model", {}, 1
        )
    assert storage.get_model_call("retry")["attempts"][0]["usage"]["input_tokens"] == 1
    current = _prepare(storage, "winner", 1)
    winner_id = current["attempts"][0]["attempt_id"]
    storage.mark_model_attempt_dispatched(winner_id)
    with pytest.raises(ValueError, match="too large"):
        storage.record_model_success(
            winner_id,
            "q1",
            _hash("input-winner"),
            _hash("large"),
            {"ok": True},
            input_tokens=1,
            output_tokens=1,
            usage={"detail": "x" * 9000},
        )
    with pytest.raises(ValueError, match="secrets"):
        storage.record_model_failure("retry:3", {"nested": {"access_token": "no"}})
    storage.record_model_success(
        winner_id,
        "q1",
        _hash("input-winner"),
        _hash("winner"),
        {"ok": True},
        input_tokens=1,
        output_tokens=1,
    )
    now = "now"
    storage._conn.execute(
        "INSERT INTO v1_model_attempts(attempt_id, call_id, attempt_no, dispatch_token, adapter, model, parameters_json, status, validation_status, reserved_tokens, created_at, updated_at) VALUES ('winner:2', 'winner', 2, 'late', 'deterministic', 'public-model', '{}', 'dispatched', 'pending', 1, ?, ?)",
        (now, now),
    )
    storage.recover_model_calls()
    assert storage.get_model_call("winner")["call"]["status"] == "succeeded"
