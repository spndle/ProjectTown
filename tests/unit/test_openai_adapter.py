from __future__ import annotations

import json

import httpx
import pytest

from backend.app.provider_secrets import (
    resolve_provider_connection,
    validate_provider_document,
)
from backend.app.v1.model_adapter import (
    KnownModelFailure,
    ModelRequest,
    UnknownModelOutcome,
)
from backend.app.v1.openai_adapter import (
    MAX_RESPONSE_BYTES,
    OPENAI_MODEL_SNAPSHOT,
    OpenAIResponsesAdapter,
    safe_audit_metadata,
)
from backend.app.v1.provider_summary import parse_structured_goal_summary

_KEY = "CANARY_API_KEY_MUST_NOT_LEAK"
_RAW = "CANARY_RAW_GOAL_MUST_NOT_LEAK"
_MODEL = "gpt-5-mini-2025-08-07"


def _connection() -> object:
    return resolve_provider_connection(
        "openai",
        environ={
            "OPENAI_BASE_URL": "https://api.openai.com/v1",
            "OPENAI_API_KEY": _KEY,
            "OPENAI_MODEL": _MODEL,
        },
    )


def _summary() -> dict[str, object]:
    return {
        "schema_version": 1,
        "task_category": "planning",
        "deliverable_kind": "implementation_plan",
        "complexity": "low",
        "acceptance_checks": ["scope_defined", "constraints_listed"],
        "allowed_tools": ["read", "check_markdown"],
        "max_steps": 3,
    }


def _request(**overrides: object) -> ModelRequest:
    summary = _summary()
    parsed = parse_structured_goal_summary(summary)
    payload: dict[str, object] = {
        "quest_id": "quest_1",
        "purpose": "planning",
        "prompt_version": "planning-v1",
        "input_hash": parsed.summary_hash,
        "contract_id": "contract_1",
        "contract_version": 1,
        "contract_hash": "b" * 64,
        "plan_id": "plan_1",
        "plan_version": 1,
        "plan_hash": "c" * 64,
        "expected_state_version": 1,
        "allowed_tools": ["read", "check_markdown"],
        "max_output_tokens": 32,
        "sanitized_parameters": {
            "structured_goal_summary": summary,
            "structured_goal_summary_hash": parsed.summary_hash,
        },
    }
    payload.update(overrides)
    return ModelRequest.model_validate(payload)


def _candidate() -> dict[str, object]:
    return {
        "schema_version": 1,
        "id": "candidate_1",
        "version": 1,
        "summary": "Synthetic candidate.",
        "steps": [
            {
                "id": "step_1",
                "title": "Inspect scope",
                "description": "Non-executing synthetic step.",
                "tool_name": "read",
                "tool_args": {},
                "dependencies": [],
            }
        ],
    }


def _success_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "model": OPENAI_MODEL_SNAPSHOT,
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": json.dumps(_candidate())}],
            }
        ],
        "usage": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
    }
    payload.update(overrides)
    return payload


def _adapter(handler: httpx.MockTransport) -> OpenAIResponsesAdapter:
    return OpenAIResponsesAdapter(connection=_connection(), transport=handler)


def test_golden_request_has_only_safe_contract_and_success_parses() -> None:
    observed: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["url"] = str(request.url)
        observed["headers"] = dict(request.headers)
        observed["body"] = json.loads(request.content)
        return httpx.Response(200, json=_success_payload())

    response = _adapter(httpx.MockTransport(handler)).create_planning_candidate(
        _request()
    )
    body = observed["body"]
    assert observed["url"] == "https://api.openai.com/v1/responses"
    assert body["model"] == OPENAI_MODEL_SNAPSHOT  # type: ignore[index]
    assert body["store"] is False  # type: ignore[index]
    assert body["text"]["format"]["strict"] is True  # type: ignore[index]
    encoded = json.dumps(body, sort_keys=True)
    for forbidden in (_RAW, "quest_1", "contract_1", "plan_1", "a" * 64, _KEY):
        assert forbidden not in encoded
    assert "authorization" not in encoded.lower()
    assert response.usage.cost_microunits == 11 * 2 + 7 * 16
    assert response.candidate.id == "candidate_1"


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, "OPENAI_BAD_REQUEST"),
        (401, "OPENAI_AUTH"),
        (403, "OPENAI_AUTH"),
        (429, "OPENAI_RATE_LIMIT"),
    ],
)
def test_known_http_failures_are_safe_and_nonleaking(status: int, code: str) -> None:
    adapter = _adapter(
        httpx.MockTransport(lambda request: httpx.Response(status, text=_RAW))
    )
    with pytest.raises(KnownModelFailure) as raised:
        adapter.create_planning_candidate(_request())
    assert raised.value.code == code
    assert _RAW not in str(raised.value)
    assert _KEY not in repr(raised.value)


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (301, "OPENAI_REDIRECT_UNKNOWN"),
        (500, "OPENAI_SERVER_UNKNOWN"),
        (503, "OPENAI_SERVER_UNKNOWN"),
    ],
)
def test_ambiguous_http_failures_are_unknown(status: int, code: str) -> None:
    adapter = _adapter(httpx.MockTransport(lambda request: httpx.Response(status)))
    with pytest.raises(UnknownModelOutcome) as raised:
        adapter.create_planning_candidate(_request())
    assert raised.value.code == code


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectTimeout("CANARY_CONNECT"),
        httpx.ReadTimeout("CANARY_READ"),
        httpx.WriteTimeout("CANARY_WRITE"),
        httpx.PoolTimeout("CANARY_POOL"),
        httpx.ConnectError("CANARY_TRANSPORT"),
    ],
)
def test_transport_failures_are_unknown_and_nonleaking(exc: Exception) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    with pytest.raises(UnknownModelOutcome) as raised:
        _adapter(httpx.MockTransport(handler)).create_planning_candidate(_request())
    assert raised.value.code in {"OPENAI_TIMEOUT_UNKNOWN", "OPENAI_TRANSPORT_UNKNOWN"}
    assert "CANARY" not in str(raised.value)


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        _success_payload(model="wrong-model"),
        _success_payload(status="incomplete"),
        _success_payload(
            output=[
                {"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}
            ]
        ),
        _success_payload(
            output=[
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": "{}"},
                        {"type": "output_text", "text": "{}"},
                    ],
                }
            ]
        ),
        _success_payload(usage=None),
        _success_payload(
            output=[
                {"type": "message", "content": [{"type": "output_text", "text": "{}"}]}
            ]
        ),
    ],
)
def test_bad_provider_envelopes_are_known_failures(payload: object) -> None:
    if isinstance(payload, bytes):
        response = httpx.Response(200, content=payload)
    else:
        response = httpx.Response(200, json=payload)
    with pytest.raises(KnownModelFailure) as raised:
        _adapter(
            httpx.MockTransport(lambda request: response)
        ).create_planning_candidate(_request())
    assert raised.value.code in {"OPENAI_INCOMPLETE", "OPENAI_MALFORMED_OUTPUT"}


@pytest.mark.parametrize(
    ("status", "error_type", "code"),
    [
        ("failed", KnownModelFailure, "OPENAI_PROVIDER_FAILED"),
        ("queued", UnknownModelOutcome, "OPENAI_NONTERMINAL_UNKNOWN"),
        ("in_progress", UnknownModelOutcome, "OPENAI_NONTERMINAL_UNKNOWN"),
        (None, KnownModelFailure, "OPENAI_MALFORMED_OUTPUT"),
        ("unexpected", KnownModelFailure, "OPENAI_MALFORMED_OUTPUT"),
    ],
)
def test_only_completed_status_can_produce_a_candidate(
    status: object, error_type: type[Exception], code: str
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_success_payload(status=status))

    with pytest.raises(error_type) as raised:
        _adapter(httpx.MockTransport(handler)).create_planning_candidate(_request())
    assert raised.value.code == code
    assert calls == 1


def test_adapter_owns_client_and_transport_seam_can_close() -> None:
    adapter = _adapter(
        httpx.MockTransport(
            lambda request: httpx.Response(200, json=_success_payload())
        )
    )
    assert adapter._client.follow_redirects is False
    adapter.close()
    assert adapter._client.is_closed is True


def test_oversize_provider_body_is_unknown() -> None:
    body = b"x" * (MAX_RESPONSE_BYTES + 1)
    with pytest.raises(UnknownModelOutcome) as raised:
        _adapter(
            httpx.MockTransport(lambda request: httpx.Response(200, content=body))
        ).create_planning_candidate(_request())
    assert raised.value.code == "OPENAI_RESPONSE_OVERSIZE_UNKNOWN"


def test_summary_binding_rejects_mismatch_and_adapter_representation_is_safe() -> None:
    request = _request()
    bad = request.model_copy(
        update={
            "sanitized_parameters": {
                **request.sanitized_parameters,
                "structured_goal_summary_hash": "d" * 64,
            }
        }
    )
    adapter = _adapter(
        httpx.MockTransport(
            lambda request: httpx.Response(200, json=_success_payload())
        )
    )
    with pytest.raises(KnownModelFailure):
        adapter.create_planning_candidate(bad)
    assert _KEY not in repr(adapter)
    assert _RAW not in repr(adapter)
    assert safe_audit_metadata(_MODEL)["model_snapshot"] == OPENAI_MODEL_SNAPSHOT


def test_summary_replacement_cannot_rebind_a_durable_input_hash() -> None:
    original = _request()
    replacement = {**_summary(), "complexity": "medium"}
    replacement_hash = parse_structured_goal_summary(replacement).summary_hash
    attacked = original.model_copy(
        update={
            "sanitized_parameters": {
                "structured_goal_summary": replacement,
                "structured_goal_summary_hash": replacement_hash,
            }
        }
    )
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_success_payload())

    with pytest.raises(KnownModelFailure) as raised:
        _adapter(httpx.MockTransport(handler)).create_planning_candidate(attacked)
    assert raised.value.code == "OPENAI_INPUT_BINDING_INVALID"
    assert calls == 0


def test_manual_connection_tampering_is_rejected_before_mock_transport() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200)

    connection = _connection()
    tampered = connection.__class__(
        provider="openai",
        base_url="https://evil.example/v1",
        api_key="bad key",
        model=_MODEL,
        source="test",
        destination_config_hash=connection.destination_config_hash,
        connection_config_hash=connection.connection_config_hash,
    )
    with pytest.raises(ValueError):
        OpenAIResponsesAdapter(
            connection=tampered, transport=httpx.MockTransport(handler)
        )
    assert calls == 0


def test_model_or_connection_hash_tampering_is_rejected_before_mock_transport() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200)

    connection = _connection()
    tampered = connection.__class__(
        provider="openai",
        base_url=connection.base_url,
        api_key=connection.api_key,
        model="qwen-plus",
        source="test",
        destination_config_hash=connection.destination_config_hash,
        connection_config_hash=connection.connection_config_hash,
    )
    with pytest.raises(ValueError):
        OpenAIResponsesAdapter(
            connection=tampered, transport=httpx.MockTransport(handler)
        )
    assert calls == 0


def test_settings_empty_key_connection_is_rejected_before_mock_transport() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200)

    connection = validate_provider_document(
        {
            "version": 3,
            "providers": {
                "openai": {
                    "base_url": "https://api.openai.com/v1",
                    "api_key": "",
                    "model": _MODEL,
                }
            },
        },
        "openai",
        allow_unconfigured_api_key=True,
    )
    with pytest.raises(ValueError):
        OpenAIResponsesAdapter(
            connection=connection, transport=httpx.MockTransport(handler)
        )
    assert calls == 0
