from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.app.v1.model_adapter import (
    MAX_CANDIDATE_BYTES,
    CandidateValidationError,
    DeterministicFakeModelAdapter,
    KnownModelFailure,
    ModelAdapter,
    ModelRequest,
    ModelUsage,
    UnknownModelOutcome,
    validate_planning_candidate,
)


def _request(input_hash: str = "a" * 64) -> ModelRequest:
    return ModelRequest(
        quest_id="quest_1",
        purpose="planning",
        prompt_version="planning-v1",
        input_hash=input_hash,
        contract_id="contract_1",
        contract_version=1,
        contract_hash="b" * 64,
        plan_id="plan_1",
        plan_version=1,
        plan_hash="c" * 64,
        expected_state_version=1,
        allowed_tools=["write_file", "check_markdown"],
        max_output_tokens=128,
        sanitized_parameters={"goal_digest": "abc"},
    )


def _candidate() -> dict[str, object]:
    return {
        "schema_version": 1,
        "id": "candidate_1",
        "version": 1,
        "summary": "safe local candidate",
        "steps": [
            {
                "id": "step_1",
                "title": "Write file",
                "tool_name": "write_file",
                "tool_args": {"path": "deliverables/brief.md"},
                "dependencies": [],
            }
        ],
    }


def _assert_rejection(
    payload: dict[str, object], code: str, allowed_tools: list[str] | None = None
) -> None:
    with pytest.raises(CandidateValidationError) as raised:
        validate_planning_candidate(
            payload, allowed_tools=allowed_tools or ["write_file"]
        )
    assert raised.value.code == code
    assert str(raised.value) == "planning candidate rejected"


def test_request_is_strict_versioned_and_bounded() -> None:
    request = _request()
    assert request.schema_version == 1
    with pytest.raises(ValidationError):
        ModelRequest.model_validate({**request.model_dump(), "unexpected": True})
    with pytest.raises(ValidationError):
        ModelRequest.model_validate({**request.model_dump(), "schema_version": 2})
    with pytest.raises(ValidationError):
        ModelRequest.model_validate(
            {**request.model_dump(), "allowed_tools": ["write_file", "write_file"]}
        )
    with pytest.raises(ValidationError):
        ModelRequest.model_validate(
            {**request.model_dump(), "sanitized_parameters": {"api_key": "not allowed"}}
        )


def test_fake_is_protocol_conformant_stable_and_input_bound() -> None:
    adapter = DeterministicFakeModelAdapter()
    assert isinstance(adapter, ModelAdapter)
    first = adapter.create_planning_candidate(_request())
    second = adapter.create_planning_candidate(_request())
    changed = adapter.create_planning_candidate(_request("d" * 64))
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.candidate_hash != changed.candidate_hash
    assert first.request_hash != changed.request_hash
    assert adapter.call_count == 3
    assert first.usage.total_tokens == first.usage.output_tokens


def test_fake_failure_classes_are_safe_and_counted() -> None:
    known_hash = "e" * 64
    unknown_hash = "f" * 64
    adapter = DeterministicFakeModelAdapter(
        known_failure_input_hashes=[known_hash],
        unknown_outcome_input_hashes=[unknown_hash],
    )
    with pytest.raises(KnownModelFailure) as known:
        adapter.create_planning_candidate(_request(known_hash))
    with pytest.raises(UnknownModelOutcome) as unknown:
        adapter.create_planning_candidate(_request(unknown_hash))
    assert known.value.code == "KNOWN_FAILURE"
    assert str(known.value) == "model adapter request failed"
    assert unknown.value.code == "UNKNOWN_OUTCOME"
    assert str(unknown.value) == "model adapter outcome is unknown"
    assert adapter.call_count == 2


def test_validator_returns_canonical_candidate_and_hash() -> None:
    validated = validate_planning_candidate(_candidate(), allowed_tools=["write_file"])
    assert validated.candidate.schema_version == 1
    assert len(validated.candidate_hash) == 64
    assert validated.candidate.steps[0].tool_args == {"path": "deliverables/brief.md"}


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda item: item.update({"schema_version": 2}), "UNSUPPORTED_SCHEMA_VERSION"),
        (lambda item: item.update({"extra": "not allowed"}), "INVALID_CANDIDATE"),
        (
            lambda item: item["steps"].append(  # type: ignore[index]
                {"id": "step_1", "title": "Duplicate", "tool_name": "write_file"}
            ),
            "DUPLICATE_STEP_ID",
        ),
        (
            lambda item: item["steps"][0].update({"dependencies": ["missing"]}),  # type: ignore[index]
            "MISSING_DEPENDENCY",
        ),
        (
            lambda item: item["steps"].update(  # type: ignore[index]
                {
                    0: {
                        **item["steps"][0],  # type: ignore[index]
                        "dependencies": ["step_2"],
                    }
                }
            ),
            "DEPENDENCY_CYCLE",
        ),
    ],
)
def test_validator_rejects_contract_violations(mutate: object, code: str) -> None:
    payload = _candidate()
    if code == "DEPENDENCY_CYCLE":
        payload["steps"] = [
            {
                "id": "step_1",
                "title": "One",
                "tool_name": "write_file",
                "dependencies": ["step_2"],
            },
            {
                "id": "step_2",
                "title": "Two",
                "tool_name": "write_file",
                "dependencies": ["step_1"],
            },
        ]
    else:
        assert callable(mutate)
        mutate(payload)
    _assert_rejection(payload, code)


@pytest.mark.parametrize(
    "path", ["/absolute.md", "../escape.md", "safe/../../escape.md", "C:\\absolute.md"]
)
def test_validator_rejects_escaping_paths(path: str) -> None:
    payload = _candidate()
    payload["steps"][0]["tool_args"] = {"path": path}  # type: ignore[index]
    _assert_rejection(payload, "UNSAFE_PATH")


@pytest.mark.parametrize(
    "tool_args",
    [
        {"api_key": "CANARY_UNIT_SECRET"},
        {"nested": {"credentials": {"authorization": "CANARY_UNIT_SECRET"}}},
        {"items": [{"apikey": "CANARY_UNIT_SECRET"}]},
    ],
)
def test_validator_rejects_sensitive_tool_args_at_any_depth(
    tool_args: dict[str, object],
) -> None:
    payload = _candidate()
    payload["steps"][0]["tool_args"] = tool_args  # type: ignore[index]
    _assert_rejection(payload, "INVALID_CANDIDATE")


def test_validator_rejects_unapproved_tool_and_oversize_candidate() -> None:
    payload = _candidate()
    _assert_rejection(payload, "TOOL_NOT_ALLOWED", allowed_tools=["check_markdown"])
    with pytest.raises(CandidateValidationError) as raised:
        validate_planning_candidate(
            _candidate(), allowed_tools=["write_file"], max_candidate_bytes=10
        )
    assert raised.value.code == "CANDIDATE_TOO_LARGE"
    assert MAX_CANDIDATE_BYTES >= 10


def test_usage_is_consistent_and_strict() -> None:
    with pytest.raises(ValidationError):
        ModelUsage(input_tokens=1, output_tokens=2, total_tokens=9, cost_microunits=0)
    with pytest.raises(ValidationError):
        ModelUsage.model_validate(
            {
                "schema_version": 1,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cost_microunits": 0,
                "extra": True,
            }
        )
