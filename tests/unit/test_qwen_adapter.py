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
from backend.app.v1.provider_summary import parse_structured_goal_summary
from backend.app.v1.qwen_adapter import MAX_RESPONSE_BYTES, QwenDashScopeAdapter

_KEY = "CANARY_QWEN_KEY_MUST_NOT_LEAK"
_RAW = "CANARY_RAW_GOAL_MUST_NOT_LEAK"
_BASE_URL = "https://workspace-1.cn-beijing.maas.aliyuncs.com/api/v1"
_MODEL = "qwen-plus"


def _connection() -> object:
    return resolve_provider_connection(
        "qwen",
        environ={
            "DASHSCOPE_BASE_URL": _BASE_URL,
            "DASHSCOPE_API_KEY": _KEY,
            "DASHSCOPE_MODEL": _MODEL,
        },
    )


def _summary() -> dict[str, object]:
    return {
        "schema_version": 1,
        "task_category": "planning",
        "deliverable_kind": "implementation_plan",
        "complexity": "low",
        "acceptance_checks": ["scope_defined"],
        "allowed_tools": ["read"],
        "max_steps": 2,
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
        "allowed_tools": ["read"],
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
                "title": "Inspect",
                "description": "Synthetic.",
                "tool_name": "read",
                "tool_args": {},
                "dependencies": [],
            },
        ],
    }


def _success(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "status_code": 200,
        "code": "",
        "output": {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(_candidate()),
                    },
                }
            ]
        },
        "usage": {"input_tokens": 11, "output_tokens": 7},
    }
    payload.update(overrides)
    return payload


def _adapter(handler: httpx.BaseTransport) -> QwenDashScopeAdapter:
    return QwenDashScopeAdapter(connection=_connection(), transport=handler)


def test_native_endpoint_headers_body_and_success_are_safe() -> None:
    observed: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["url"] = str(request.url)
        observed["headers"] = dict(request.headers)
        observed["body"] = json.loads(request.content)
        return httpx.Response(200, json=_success())

    response = _adapter(httpx.MockTransport(handler)).create_planning_candidate(
        _request()
    )
    assert observed["url"] == _BASE_URL + "/services/aigc/text-generation/generation"
    headers, body = observed["headers"], observed["body"]
    assert headers["authorization"] == f"Bearer {_KEY}"  # type: ignore[index]
    assert body["model"] == _MODEL and body["parameters"] == {
        "result_format": "message",
        "response_format": {"type": "json_object"},
        "enable_thinking": False,
        "max_tokens": 32,
    }  # type: ignore[index]
    encoded = json.dumps(body, sort_keys=True)  # type: ignore[arg-type]
    for forbidden in (_RAW, "quest_1", "contract_1", "plan_1", _KEY):
        assert forbidden not in encoded
    assert (
        response.candidate.id == "candidate_1"
        and response.usage.total_tokens == 18
        and response.usage.cost_microunits == 25
    )


@pytest.mark.parametrize(
    "status,code,error",
    [
        (400, "QWEN_BAD_REQUEST", KnownModelFailure),
        (401, "QWEN_AUTH", KnownModelFailure),
        (429, "QWEN_RATE_LIMIT", KnownModelFailure),
        (500, "QWEN_SERVER_UNKNOWN", UnknownModelOutcome),
        (302, "QWEN_REDIRECT_UNKNOWN", UnknownModelOutcome),
    ],
)
def test_http_statuses_are_safe(status: int, code: str, error: type[Exception]) -> None:
    with pytest.raises(error) as raised:
        _adapter(
            httpx.MockTransport(lambda request: httpx.Response(status, text=_RAW))
        ).create_planning_candidate(_request())
    assert (
        raised.value.code == code
        and _RAW not in str(raised.value)
        and _KEY not in repr(raised.value)
    )


@pytest.mark.parametrize(
    "exc,code",
    [
        (httpx.ConnectTimeout("CANARY_TIMEOUT"), "QWEN_TIMEOUT_UNKNOWN"),
        (httpx.ConnectError("CANARY_TRANSPORT"), "QWEN_TRANSPORT_UNKNOWN"),
    ],
)
def test_transport_errors_are_unknown_without_leaks(exc: Exception, code: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    with pytest.raises(UnknownModelOutcome) as raised:
        _adapter(httpx.MockTransport(handler)).create_planning_candidate(_request())
    assert raised.value.code == code and "CANARY" not in str(raised.value)


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        {},
        {"output": {"choices": []}, "usage": {"input_tokens": 1, "output_tokens": 1}},
        _success(usage=None),
        _success(
            output={
                "choices": [
                    {"message": {"content": "{}"}},
                    {"message": {"content": "{}"}},
                ]
            }
        ),
    ],
)
def test_malformed_provider_envelopes_are_known(payload: object) -> None:
    response = (
        httpx.Response(200, content=payload)
        if isinstance(payload, bytes)
        else httpx.Response(200, json=payload)
    )
    with pytest.raises(KnownModelFailure) as raised:
        _adapter(
            httpx.MockTransport(lambda request: response)
        ).create_planning_candidate(_request())
    assert raised.value.code == "QWEN_MALFORMED_OUTPUT"


@pytest.mark.parametrize(
    "payload",
    [
        {key: value for key, value in _success().items() if key != "status_code"},
        _success(status_code=None),
        _success(status_code=201),
        _success(status_code=True),
        _success(code=200),
        _success(code="BadRequest"),
        _success(
            output={
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(_candidate()),
                        },
                    }
                ]
            }
        ),
        _success(
            output={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "user",
                            "content": json.dumps(_candidate()),
                        },
                    }
                ]
            }
        ),
        _success(
            output={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(_candidate()),
                            "reasoning_content": "hidden",
                        },
                    }
                ]
            }
        ),
        _success(
            output={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(_candidate()),
                            "tool_calls": [],
                        },
                    }
                ]
            }
        ),
    ],
)
def test_nonterminal_or_tool_envelopes_are_rejected(payload: object) -> None:
    with pytest.raises(KnownModelFailure) as raised:
        _adapter(
            httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
        ).create_planning_candidate(_request())
    assert raised.value.code == "QWEN_MALFORMED_OUTPUT"


def test_oversize_is_unknown_and_client_is_hardened() -> None:
    with pytest.raises(UnknownModelOutcome) as raised:
        _adapter(
            httpx.MockTransport(
                lambda request: httpx.Response(
                    200, content=b"x" * (MAX_RESPONSE_BYTES + 1)
                )
            )
        ).create_planning_candidate(_request())
    assert raised.value.code == "QWEN_RESPONSE_OVERSIZE_UNKNOWN"
    adapter = _adapter(
        httpx.MockTransport(lambda request: httpx.Response(200, json=_success()))
    )
    assert adapter._client.follow_redirects is False
    adapter.close()
    assert adapter._client.is_closed


def test_tampered_or_empty_connection_never_reaches_transport() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200)

    connection = _connection()
    tampered = connection.__class__(
        provider="qwen",
        base_url="https://evil.example/api/v1",
        api_key="bad",
        model=_MODEL,
        source="test",
        destination_config_hash=connection.destination_config_hash,
        connection_config_hash=connection.connection_config_hash,
    )
    with pytest.raises(ValueError):
        QwenDashScopeAdapter(
            connection=tampered, transport=httpx.MockTransport(handler)
        )
    empty = validate_provider_document(
        {
            "version": 3,
            "providers": {
                "qwen": {"base_url": _BASE_URL, "api_key": "", "model": _MODEL}
            },
        },
        "qwen",
        allow_unconfigured_api_key=True,
    )
    with pytest.raises(ValueError):
        QwenDashScopeAdapter(connection=empty, transport=httpx.MockTransport(handler))
    assert calls == 0


def test_summary_binding_rejects_before_transport_and_repr_is_safe() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_success())

    adapter = _adapter(httpx.MockTransport(handler))
    request = _request().model_copy(
        update={
            "sanitized_parameters": {
                "structured_goal_summary": _summary(),
                "structured_goal_summary_hash": "d" * 64,
            }
        }
    )
    with pytest.raises(KnownModelFailure) as raised:
        adapter.create_planning_candidate(request)
    assert raised.value.code == "QWEN_SUMMARY_HASH_INVALID" and calls == 0
    assert _KEY not in repr(adapter) and _RAW not in repr(adapter)
