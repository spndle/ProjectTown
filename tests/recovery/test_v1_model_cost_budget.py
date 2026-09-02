from __future__ import annotations

import threading
from hashlib import sha256

import pytest

from backend.app.v1.storage import (
    PHASE1C_MAX_TOTAL_MICRO_CNY,
    QWEN_COST_ACCOUNT_ID,
    QWEN_MAX_TOTAL_MICRO_CNY,
    V1Storage,
)


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _draft(storage: V1Storage, quest_id: str) -> None:
    storage.create_draft(
        quest_id,
        {
            "id": f"contract-{quest_id}",
            "version": 1,
            "goal": "cost audit",
            "budget": {"max_tokens": 100_000},
        },
        {"id": f"plan-{quest_id}", "version": 1, "milestones": [{"id": "one"}]},
    )


def _prepare(
    storage: V1Storage,
    quest_id: str,
    call_id: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 1,
) -> dict:
    state = storage.require_quest(quest_id)
    return storage.prepare_model_call(
        call_id,
        quest_id,
        "phase1c-eval",
        f"idem-{call_id}",
        _hash(f"input-{call_id}"),
        "phase1c-structured-v1",
        1,
        1,
        "openai",
        "gpt-5-mini-2025-08-07",
        {},
        input_tokens + output_tokens,
        expected_state_version=state["state_version"],
        dispatch_token=f"dispatch-{call_id}",
        cost_reservation=V1Storage.phase1c_cost_reservation(
            input_tokens, output_tokens
        ),
    )


def test_cost_reservation_per_call_unknown_restart_and_hash_only(tmp_path) -> None:
    path = tmp_path / "cost.db"
    storage = V1Storage(path)
    _draft(storage, "q1")
    with pytest.raises(ValueError, match="per-call"):
        _prepare(storage, "q1", "too-large", output_tokens=31_251)
    prepared = _prepare(storage, "q1", "unknown", input_tokens=4, output_tokens=5)
    attempt_id = prepared["attempts"][0]["attempt_id"]
    storage.mark_model_attempt_dispatched(attempt_id)
    storage.mark_model_attempt_unknown(attempt_id, {"code": "TIMEOUT"})
    usage = storage.model_cost_usage()
    assert usage["held_micro_cny"] == 88
    storage.close()

    storage = V1Storage(path)
    assert storage.model_cost_usage()["held_micro_cny"] == 88
    hash_only = _prepare(storage, "q1", "hash-only")
    hash_attempt = hash_only["attempts"][0]["attempt_id"]
    storage.mark_model_attempt_dispatched(hash_attempt)
    stored = storage.record_model_success(
        hash_attempt,
        "q1",
        _hash("input-hash-only"),
        _hash("response-hash-only"),
        {"summary": "candidate that must not be retained"},
        input_tokens=0,
        output_tokens=1,
        retain_candidate=False,
    )
    assert stored["candidate"] is None
    assert stored["candidate_hash"] == _hash(
        '{"summary":"candidate that must not be retained"}'
    )
    assert (
        storage._conn.execute(
            "SELECT candidate_json FROM v1_model_attempts WHERE attempt_id=?",
            (hash_attempt,),
        ).fetchone()[0]
        is None
    )
    storage.close()


def test_actual_over_reservation_breaches_and_blocks_future_calls(tmp_path) -> None:
    storage = V1Storage(tmp_path / "breach.db")
    _draft(storage, "q1")
    prepared = _prepare(storage, "q1", "breach", output_tokens=1)
    attempt_id = prepared["attempts"][0]["attempt_id"]
    storage.mark_model_attempt_dispatched(attempt_id)
    storage.record_model_success(
        attempt_id,
        "q1",
        _hash("input-breach"),
        _hash("response-breach"),
        {"ok": True},
        input_tokens=9,
        output_tokens=0,
    )
    usage = storage.model_cost_usage()
    assert usage["settled_micro_cny"] == 18
    assert usage["breached"] is True
    with pytest.raises(ValueError, match="breached"):
        _prepare(storage, "q1", "blocked")
    storage.close()


def test_multi_connection_reservation_never_exceeds_global_budget(tmp_path) -> None:
    path = tmp_path / "concurrent.db"
    setup = V1Storage(path)
    # 41 requests x 500,000 micro-CNY: exactly 40 may reserve.
    for number in range(41):
        _draft(setup, f"q{number}")
    setup.close()
    barrier = threading.Barrier(41)
    results: list[str] = []
    result_lock = threading.Lock()

    def reserve(number: int) -> None:
        database = V1Storage(path)
        try:
            barrier.wait(timeout=20)
            _prepare(database, f"q{number}", f"call-{number}", output_tokens=31_250)
            result = "accepted"
        except ValueError:
            result = "rejected"
        finally:
            database.close()
        with result_lock:
            results.append(result)

    threads = [threading.Thread(target=reserve, args=(number,)) for number in range(41)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert len(results) == 41
    assert results.count("accepted") == 40
    assert results.count("rejected") == 1
    verify = V1Storage(path)
    usage = verify.model_cost_usage()
    assert usage["held_micro_cny"] == PHASE1C_MAX_TOTAL_MICRO_CNY
    assert usage["available_micro_cny"] == 0
    verify.close()


def test_qwen_cost_profile_is_separate_and_settles_native_micro_cny(tmp_path) -> None:
    storage = V1Storage(tmp_path / "qwen-cost.db")
    _draft(storage, "qwen-q1")
    state = storage.require_quest("qwen-q1")
    quote = V1Storage.phase1c_cost_reservation(4, 5, provider="qwen", model="qwen-plus")
    assert quote["account_id"] == QWEN_COST_ACCOUNT_ID
    assert quote["reserved_micro_cny"] == 44  # 4*1 + 5*8 defensive reserve
    prepared = storage.prepare_model_call(
        "qwen-call",
        "qwen-q1",
        "phase2-qwen",
        "idem-qwen",
        _hash("qwen-input"),
        "phase2-qwen-v1",
        1,
        1,
        "qwen-dashscope-native",
        "qwen-plus",
        {},
        9,
        expected_state_version=state["state_version"],
        dispatch_token="dispatch-qwen",
        cost_reservation=quote,
    )
    attempt_id = prepared["attempts"][0]["attempt_id"]
    storage.mark_model_attempt_dispatched(attempt_id)
    storage.record_model_failure(
        attempt_id, {"code": "QWEN_RATE_LIMIT"}, input_tokens=4, output_tokens=5
    )
    qwen_usage = storage.model_cost_usage(provider="qwen", model="qwen-plus")
    openai_usage = storage.model_cost_usage()
    assert qwen_usage["settled_micro_cny"] == 14  # 4*1 + 5*2 actual native cost
    assert qwen_usage["max_total_micro_cny"] == QWEN_MAX_TOTAL_MICRO_CNY
    assert openai_usage["settled_micro_cny"] == 0
    storage.close()
