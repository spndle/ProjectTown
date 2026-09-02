from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from pydantic import ValidationError

from backend.app.runtime import stable_hash
from backend.app.v1.model_adapter import (
    DeterministicFakeModelAdapter,
    ModelResponse,
    PlanningCandidate,
    PlanningStepCandidate,
)
from backend.app.v1.model_runtime import (
    ModelCallCoordinator,
    ModelCallResult,
    ModelCallSubmission,
)
from backend.app.v1.storage import V1Storage
from tests.conftest import APIContext


def _planned(api: APIContext, *, max_tokens: int = 200) -> dict[str, Any]:
    created = api.client.post(
        "/api/v2/quests",
        json={
            "goal": "Create a local model runtime test brief",
            "template_id": "project_brief",
            "budget": {"max_tokens": max_tokens},
        },
    )
    assert created.status_code == 201, created.text
    draft = created.json()
    confirmed = api.client.post(
        f"/api/v2/quests/{draft['id']}/confirm",
        json={
            "expected_state_version": draft["state_version"],
            "expected_contract_version": 1,
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()


def _run(
    coordinator: ModelCallCoordinator,
    quest: dict[str, Any],
    *,
    key: str = "model-call-1",
    payload: dict[str, Any] | None = None,
    parameters: dict[str, Any] | None = None,
    reserve: int = 32,
    cost_reservation: dict[str, Any] | None = None,
) -> ModelCallResult:
    return coordinator.run(
        quest_id=quest["id"],
        idempotency_key=key,
        prompt_version="planning-v1",
        input_payload=payload or {"goal": "local brief"},
        allowed_tools=["write_file"],
        sanitized_parameters=parameters or {"goal_digest": "d" * 64},
        reserved_tokens=reserve,
        max_output_tokens=16,
        expected_state_version=quest["state_version"],
        adapter_label="deterministic-fake",
        model_label="offline-v1",
        cost_reservation=cost_reservation,
    )


def test_success_is_validated_current_and_persists_only_auditable_data(
    api: APIContext,
) -> None:
    quest = _planned(api)
    adapter = DeterministicFakeModelAdapter()
    coordinator = ModelCallCoordinator(api.app.state.runtime_storage, adapter)
    before_events = api.app.state.runtime_storage.list_events(quest["id"])
    before = api.app.state.runtime_storage.get_quest(quest["id"])

    result = _run(coordinator, quest)

    assert result.outcome == "validated_current"
    assert result.validation_status == "validated_current"
    assert result.candidate_hash and result.response_hash
    assert adapter.call_count == 1
    record = api.app.state.runtime_storage.get_model_call(result.call_id or "")
    assert record is not None
    attempt = record["attempts"][0]
    assert attempt["parameters"] == {
        "schema_version": 1,
        "allowed_tools": ["write_file"],
        "max_tokens": 16,
        "sanitized_parameters": {"goal_digest": "d" * 64},
    }
    assert "local brief" not in repr(record)
    assert attempt["candidate_hash"] == result.candidate_hash
    after = api.app.state.runtime_storage.get_quest(quest["id"])
    assert api.app.state.runtime_storage.list_events(quest["id"]) == before_events
    assert after["contract"] == before["contract"]
    assert after["plan_id"] == before["plan_id"]


def test_budget_rejection_creates_no_attempt_and_does_not_dispatch(
    api: APIContext,
) -> None:
    quest = _planned(api, max_tokens=8)
    adapter = DeterministicFakeModelAdapter()
    coordinator = ModelCallCoordinator(api.app.state.runtime_storage, adapter)

    result = _run(coordinator, quest, reserve=32)

    assert result.outcome == "budget_rejected"
    assert result.error_code == "BUDGET_REJECTED"
    assert adapter.call_count == 0
    assert api.app.state.runtime_storage.list_model_calls(quest["id"]) == []


def test_known_failure_releases_reservation_and_unknown_holds_it(
    api: APIContext,
) -> None:
    quest = _planned(api)
    known_payload = {"goal": "known"}
    unknown_payload = {"goal": "unknown"}
    adapter = DeterministicFakeModelAdapter(
        known_failure_input_hashes=[stable_hash(known_payload)],
        unknown_outcome_input_hashes=[stable_hash(unknown_payload)],
    )
    coordinator = ModelCallCoordinator(api.app.state.runtime_storage, adapter)

    known = _run(coordinator, quest, key="known", payload=known_payload)
    after_known = api.app.state.runtime_storage.model_token_usage(quest["id"])
    unknown = _run(coordinator, quest, key="unknown", payload=unknown_payload)
    after_unknown = api.app.state.runtime_storage.model_token_usage(quest["id"])

    assert known.outcome == "failed"
    assert known.error_code == "KNOWN_FAILURE"
    assert after_known["held_tokens"] == 0
    assert after_known["settled_tokens"] == 0
    assert unknown.outcome == "unknown_outcome"
    assert unknown.error_code == "UNKNOWN_OUTCOME"
    assert after_unknown["held_tokens"] == 32
    assert adapter.call_count == 2


class _MismatchedHashAdapter:
    def __init__(self) -> None:
        self._delegate = DeterministicFakeModelAdapter()
        self.call_count = 0

    def create_planning_candidate(self, request):  # type: ignore[no-untyped-def]
        self.call_count += 1
        payload = self._delegate.create_planning_candidate(request).model_dump(
            mode="json"
        )
        payload["request_hash"] = "0" * 64
        return payload


class _InvalidCandidateAdapter:
    def __init__(self) -> None:
        self._delegate = DeterministicFakeModelAdapter()
        self.call_count = 0

    def create_planning_candidate(self, request):  # type: ignore[no-untyped-def]
        self.call_count += 1
        response = self._delegate.create_planning_candidate(request)
        candidate = PlanningCandidate(
            id=response.candidate.id,
            version=response.candidate.version,
            summary=response.candidate.summary,
            steps=[
                PlanningStepCandidate(
                    id=response.candidate.steps[0].id,
                    title=response.candidate.steps[0].title,
                    description=response.candidate.steps[0].description,
                    tool_name="not_allowlisted",
                    tool_args=response.candidate.steps[0].tool_args,
                )
            ],
        )
        return ModelResponse(
            request_hash=response.request_hash,
            candidate=candidate,
            candidate_hash=stable_hash(candidate.model_dump(mode="json")),
            usage=response.usage,
        )


class _SensitiveToolArgsAdapter:
    def __init__(self, canary: str) -> None:
        self._delegate = DeterministicFakeModelAdapter()
        self._canary = canary
        self.call_count = 0

    def create_planning_candidate(self, request):  # type: ignore[no-untyped-def]
        self.call_count += 1
        payload = self._delegate.create_planning_candidate(request).model_dump(
            mode="json"
        )
        payload["candidate"]["steps"][0]["tool_args"] = {
            "nested": {"api_key": self._canary}
        }
        payload["candidate_hash"] = stable_hash(payload["candidate"])
        return payload


def test_invalid_or_mismatched_response_settles_actual_usage(api: APIContext) -> None:
    quest = _planned(api)
    mismatch_adapter = _MismatchedHashAdapter()
    mismatch = _run(
        ModelCallCoordinator(api.app.state.runtime_storage, mismatch_adapter),
        quest,
        key="mismatch",
    )
    invalid_adapter = _InvalidCandidateAdapter()
    invalid = _run(
        ModelCallCoordinator(api.app.state.runtime_storage, invalid_adapter),
        quest,
        key="invalid-candidate",
    )
    usage = api.app.state.runtime_storage.model_token_usage(quest["id"])

    assert mismatch.outcome == "failed"
    assert mismatch.error_code == "REQUEST_HASH_MISMATCH"
    assert invalid.outcome == "failed"
    assert invalid.error_code == "CANDIDATE_REJECTED"
    assert usage["settled_tokens"] == 32
    assert usage["held_tokens"] == 0
    assert mismatch_adapter.call_count == 1
    assert invalid_adapter.call_count == 1


def test_sensitive_tool_args_are_rejected_and_never_persisted(api: APIContext) -> None:
    quest = _planned(api)
    canary = "CANARY_RUNTIME_SECRET_MUST_NOT_PERSIST"
    adapter = _SensitiveToolArgsAdapter(canary)
    storage = api.app.state.runtime_storage

    result = _run(
        ModelCallCoordinator(storage, adapter), quest, key="sensitive-tool-args"
    )

    assert result.outcome == "failed"
    assert result.error_code == "INVALID_RESPONSE"
    assert adapter.call_count == 1
    record = storage.get_model_call(result.call_id or "")
    assert record is not None
    assert record["attempts"][0]["candidate"] is None
    rows = storage._conn.execute(
        "SELECT candidate_json FROM v1_model_attempts WHERE candidate_json LIKE ?",
        (f"%{canary}%",),
    ).fetchall()
    assert rows == []


class _DriftAdapter:
    def __init__(self, storage, quest_id: str, state_version: int) -> None:  # type: ignore[no-untyped-def]
        self._storage = storage
        self._quest_id = quest_id
        self._state_version = state_version
        self._delegate = DeterministicFakeModelAdapter()
        self.call_count = 0

    def create_planning_candidate(self, request):  # type: ignore[no-untyped-def]
        self.call_count += 1
        self._storage.append_event(
            self._quest_id,
            "ModelRuntimeTestDrift",
            {"model_runtime_test_drift": True},
            self._state_version,
        )
        return self._delegate.create_planning_candidate(request)


def test_external_state_drift_returns_stale_without_plan_or_contract_change(
    api: APIContext,
) -> None:
    quest = _planned(api)
    storage = api.app.state.runtime_storage
    before = storage.get_quest(quest["id"])
    before_events = storage.list_events(quest["id"])
    adapter = _DriftAdapter(storage, quest["id"], quest["state_version"])

    result = _run(ModelCallCoordinator(storage, adapter), quest)
    after = storage.get_quest(quest["id"])

    assert result.outcome == "stale"
    assert result.validation_status == "stale"
    assert len(storage.list_events(quest["id"])) == len(before_events) + 1
    assert after["contract"] == before["contract"]
    assert after["plan_id"] == before["plan_id"]
    assert after["plan_version"] == before["plan_version"]


def test_duplicate_call_is_serialized_and_never_invokes_adapter_twice(
    api: APIContext,
) -> None:
    quest = _planned(api)
    adapter = DeterministicFakeModelAdapter()
    coordinator = ModelCallCoordinator(api.app.state.runtime_storage, adapter)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(lambda _: _run(coordinator, quest, key="same"), range(2))
        )

    assert adapter.call_count == 1
    assert {result.outcome for result in results} == {"validated_current"}
    assert sum(result.idempotent_replay for result in results) == 1


def test_two_coordinators_share_process_deduplication(api: APIContext) -> None:
    quest = _planned(api)
    adapter = DeterministicFakeModelAdapter()
    storage = api.app.state.runtime_storage
    first = ModelCallCoordinator(storage, adapter)
    second = ModelCallCoordinator(storage, adapter)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda coordinator: _run(coordinator, quest, key="cross-instance"),
                (first, second),
            )
        )

    assert adapter.call_count == 1
    assert {result.outcome for result in results} == {"validated_current"}
    assert sum(result.idempotent_replay for result in results) == 1


def test_replay_selects_latest_then_winning_attempt_without_dispatch(
    api: APIContext,
) -> None:
    quest = _planned(api)
    payload = {"goal": "retry binding"}
    adapter = DeterministicFakeModelAdapter(
        known_failure_input_hashes=[stable_hash(payload)]
    )
    storage = api.app.state.runtime_storage
    coordinator = ModelCallCoordinator(storage, adapter)
    first = _run(coordinator, quest, key="manual-retry", payload=payload)
    assert first.outcome == "failed"
    first_record = storage.get_model_call(first.call_id or "")
    assert first_record is not None
    first_attempt = first_record["attempts"][0]
    retry_id = "manual-retry:2"
    storage.retry_model_call(
        first.call_id or "",
        retry_id,
        "manual-retry-token-2",
        first_attempt["adapter"],
        first_attempt["model"],
        first_attempt["parameters"],
        first_attempt["reserved_tokens"],
    )

    latest = _run(coordinator, quest, key="manual-retry", payload=payload)
    assert latest.outcome == "in_progress"
    assert latest.attempt_id == retry_id
    assert latest.idempotent_replay is True
    assert adapter.call_count == 1

    storage.mark_model_attempt_dispatched(retry_id)
    candidate = {
        "schema_version": 1,
        "id": "candidate_retry",
        "version": 1,
        "summary": "manual retry candidate",
        "steps": [
            {
                "id": "step_retry",
                "title": "Write file",
                "tool_name": "write_file",
                "tool_args": {"path": "deliverables/retry.md"},
                "dependencies": [],
            }
        ],
    }
    storage.record_model_success(
        retry_id,
        quest["id"],
        first_record["call"]["input_hash"],
        "a" * 64,
        candidate,
        input_tokens=0,
        output_tokens=1,
        usage={
            "schema_version": 1,
            "input_tokens": 0,
            "output_tokens": 1,
            "total_tokens": 1,
            "cost_microunits": 0,
        },
        cost={"cost_microunits": 0},
    )

    winner = _run(coordinator, quest, key="manual-retry", payload=payload)
    assert winner.outcome == "validated_current"
    assert winner.attempt_id == retry_id
    assert winner.idempotent_replay is True
    assert adapter.call_count == 1


class _TamperedPrepareStorage:
    def __init__(self, storage) -> None:  # type: ignore[no-untyped-def]
        self._storage = storage

    def prepare_model_call(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        prepared = self._storage.prepare_model_call(*args, **kwargs)
        prepared["attempts"][0]["parameters"] = {"schema_version": 1}
        return prepared

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        return getattr(self._storage, name)


def test_invalid_durable_attempt_parameters_fail_closed_before_dispatch(
    api: APIContext,
) -> None:
    quest = _planned(api)
    adapter = DeterministicFakeModelAdapter()
    storage = api.app.state.runtime_storage
    coordinator = ModelCallCoordinator(_TamperedPrepareStorage(storage), adapter)

    result = _run(coordinator, quest, key="tampered-durable")

    assert result.outcome == "failed"
    assert result.error_code == "DURABLE_PARAMETERS_INVALID"
    assert adapter.call_count == 0
    record = storage.get_model_call(result.call_id or "")
    assert record is not None
    attempt = record["attempts"][0]
    assert attempt["status"] == "failed"
    assert attempt["dispatched_at"] is None


def test_secrets_are_rejected_before_dispatch(api: APIContext) -> None:
    quest = _planned(api)
    adapter = DeterministicFakeModelAdapter()
    coordinator = ModelCallCoordinator(api.app.state.runtime_storage, adapter)

    result = _run(
        coordinator,
        quest,
        payload={"api_key": "not persisted"},
    )

    assert result.outcome == "invalid"
    assert result.error_code == "INVALID_REQUEST"
    assert adapter.call_count == 0
    assert api.app.state.runtime_storage.list_model_calls(quest["id"]) == []


@pytest.mark.parametrize("key", ["token", "ACCESS-TOKEN", "nested_token"])
def test_token_parameters_are_rejected_before_dispatch_and_persistence(
    api: APIContext, key: str
) -> None:
    quest = _planned(api)
    adapter = DeterministicFakeModelAdapter()
    result = _run(
        ModelCallCoordinator(api.app.state.runtime_storage, adapter),
        quest,
        key=f"sensitive-{key}",
        parameters={key: "must-not-dispatch"},
    )
    assert result.outcome == "invalid"
    assert result.error_code == "INVALID_REQUEST"
    assert adapter.call_count == 0
    assert api.app.state.runtime_storage.list_model_calls(quest["id"]) == []


@pytest.mark.parametrize(
    "field",
    [
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "reserved_tokens",
        "settled_tokens",
        "max_tokens",
        "estimated_input_tokens",
        "max_output_tokens",
    ],
)
def test_public_token_counters_allow_non_negative_integers(
    api: APIContext, field: str
) -> None:
    quest = _planned(api)
    submission = ModelCallSubmission(
        quest_id=quest["id"],
        idempotency_key=f"public-counter-{field}",
        prompt_version="planning-v1",
        input_payload={"goal": "local brief"},
        allowed_tools=["write_file"],
        sanitized_parameters={field: 0},
        reserved_tokens=32,
        max_output_tokens=16,
        expected_state_version=quest["state_version"],
        adapter_label="deterministic-fake",
        model_label="offline-v1",
    )
    assert submission.sanitized_parameters == {field: 0}


@pytest.mark.parametrize("value", ["1", -1, True])
def test_public_token_counter_rejects_non_numeric_values(
    api: APIContext, value: Any
) -> None:
    quest = _planned(api)
    adapter = DeterministicFakeModelAdapter()
    result = _run(
        ModelCallCoordinator(api.app.state.runtime_storage, adapter),
        quest,
        key=f"invalid-counter-{type(value).__name__}-{value}",
        parameters={"input_tokens": value},
    )
    assert result.outcome == "invalid"
    assert result.error_code == "INVALID_REQUEST"
    assert adapter.call_count == 0
    assert api.app.state.runtime_storage.list_model_calls(quest["id"]) == []


def test_phase1c_cost_reservation_token_counters_submit_with_mock_adapter(
    api: APIContext,
) -> None:
    quest = _planned(api, max_tokens=10_000)
    adapter = DeterministicFakeModelAdapter()
    result = _run(
        ModelCallCoordinator(api.app.state.runtime_storage, adapter),
        quest,
        key="phase1c-cost-reservation",
        reserve=4_608,
        cost_reservation=V1Storage.phase1c_cost_reservation(4096, 512),
    )
    assert result.outcome == "validated_current"
    assert adapter.call_count == 1


def test_default_app_create_confirm_and_replay_do_not_use_model_runtime(
    api: APIContext,
) -> None:
    quest = _planned(api)
    adapter = DeterministicFakeModelAdapter()
    storage = api.app.state.runtime_storage

    replayed = storage.replay(quest["id"])

    assert replayed["id"] == quest["id"]
    assert storage.list_model_calls(quest["id"]) == []
    assert adapter.call_count == 0


def test_result_dto_is_strict() -> None:
    with pytest.raises(ValidationError):
        ModelCallResult.model_validate(
            {
                "outcome": "invalid",
                "validation_status": "invalid",
                "extra": True,
            }
        )
