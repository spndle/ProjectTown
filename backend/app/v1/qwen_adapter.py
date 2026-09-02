"""Narrow native DashScope Generation adapter for isolated Qwen planning evals."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Final

import httpx
from pydantic import ValidationError

from ..provider_secrets import (
    ResolvedProviderConnection,
    SecretResolutionError,
    _connection_config_hash,
    _destination_hash,
    _validate_api_key,
    _validate_destination,
    _validate_model_value,
)
from ..runtime import stable_hash
from .model_adapter import (
    CandidateValidationError,
    KnownModelFailure,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    PlanningCandidate,
    UnknownModelOutcome,
    validate_planning_candidate,
)
from .prompt_registry import FIXED_INSTRUCTIONS
from .provider_summary import SummaryValidationError, parse_structured_goal_summary

QWEN_MODEL_SNAPSHOT: Final = "qwen-plus"
QWEN_ADAPTER_PROTOCOL: Final = "dashscope-generation-v1"
MAX_RESPONSE_BYTES: Final = 131_072
MAX_PROVIDER_OUTPUT_TOKENS: Final = 30_000
QWEN_INPUT_MICRO_CNY_PER_TOKEN: Final = 1
QWEN_OUTPUT_MICRO_CNY_PER_TOKEN: Final = 2
_GENERATION_SUFFIX: Final = "/services/aigc/text-generation/generation"
_SUMMARY_KEY: Final = "structured_goal_summary"
_SUMMARY_HASH_KEY: Final = "structured_goal_summary_hash"


class QwenKnownFailure(KnownModelFailure):
    def __init__(self, code: str) -> None:
        self.code = code
        RuntimeError.__init__(self, "Qwen adapter request failed")


class QwenUnknownOutcome(UnknownModelOutcome):
    def __init__(self, code: str) -> None:
        self.code = code
        RuntimeError.__init__(self, "Qwen adapter outcome is unknown")


class QwenDashScopeAdapter:
    """Synchronous native DashScope Generation adapter with safe error surfaces."""

    def __init__(
        self,
        *,
        connection: ResolvedProviderConnection,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if (
            not isinstance(connection, ResolvedProviderConnection)
            or connection.provider != "qwen"
        ):
            raise ValueError("Qwen connection is required")
        try:
            base_url = _validate_destination("qwen", connection.base_url)
            _validate_api_key(connection.api_key)
            model = _validate_model_value(connection.model, "qwen")
        except SecretResolutionError:
            raise ValueError("Qwen destination is denied") from None
        if connection.destination_config_hash != _destination_hash("qwen", base_url):
            raise ValueError("Qwen connection is required")
        if connection.connection_config_hash != _connection_config_hash(
            "qwen", base_url, model
        ):
            raise ValueError("Qwen connection is required")
        self._api_key = connection.api_key
        self._model = model
        self._generation_url = f"{base_url}{_GENERATION_SUFFIX}"
        self._client = httpx.Client(
            timeout=httpx.Timeout(connect=5.0, read=20.0, write=5.0, pool=5.0),
            trust_env=False,
            follow_redirects=False,
            transport=transport,
        )

    def __repr__(self) -> str:
        return f"QwenDashScopeAdapter(model={self._model})"

    def close(self) -> None:
        self._client.close()

    def create_planning_candidate(self, request: ModelRequest) -> ModelResponse:
        summary = _summary_bound_to_request(request)
        if request.max_output_tokens > MAX_PROVIDER_OUTPUT_TOKENS:
            raise QwenKnownFailure("QWEN_MAX_OUTPUT_INVALID")
        body = _build_request_body(
            summary.canonical_payload(), request.max_output_tokens, self._model
        )
        try:
            with self._client.stream(
                "POST",
                self._generation_url,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                content=json.dumps(
                    body, separators=(",", ":"), ensure_ascii=False
                ).encode("utf-8"),
            ) as response:
                status_code = response.status_code
                if status_code == 400:
                    raise QwenKnownFailure("QWEN_BAD_REQUEST")
                if status_code in {401, 403}:
                    raise QwenKnownFailure("QWEN_AUTH")
                if status_code == 429:
                    raise QwenKnownFailure("QWEN_RATE_LIMIT")
                if status_code >= 500:
                    raise QwenUnknownOutcome("QWEN_SERVER_UNKNOWN")
                if 300 <= status_code < 400:
                    raise QwenUnknownOutcome("QWEN_REDIRECT_UNKNOWN")
                if status_code != 200:
                    raise QwenKnownFailure("QWEN_HTTP_FAILURE")
                response_bytes = _read_limited(response)
        except httpx.TimeoutException:
            raise QwenUnknownOutcome("QWEN_TIMEOUT_UNKNOWN") from None
        except httpx.TransportError:
            raise QwenUnknownOutcome("QWEN_TRANSPORT_UNKNOWN") from None
        try:
            payload = json.loads(response_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise QwenKnownFailure("QWEN_MALFORMED_OUTPUT") from None
        return _parse_response(payload, request)


def _summary_bound_to_request(request: ModelRequest):
    parameters = request.sanitized_parameters
    if set(parameters) != {_SUMMARY_KEY, _SUMMARY_HASH_KEY}:
        raise QwenKnownFailure("QWEN_SUMMARY_PARAMETERS_INVALID")
    try:
        summary = parse_structured_goal_summary(parameters[_SUMMARY_KEY])
    except SummaryValidationError:
        raise QwenKnownFailure("QWEN_SUMMARY_PARAMETERS_INVALID") from None
    supplied_hash = parameters[_SUMMARY_HASH_KEY]
    if not isinstance(supplied_hash, str) or supplied_hash != summary.summary_hash:
        raise QwenKnownFailure("QWEN_SUMMARY_HASH_INVALID")
    if request.input_hash != summary.summary_hash:
        raise QwenKnownFailure("QWEN_INPUT_BINDING_INVALID")
    return summary


def _build_request_body(
    summary: dict[str, Any], max_output_tokens: int, model: str
) -> dict[str, Any]:
    return {
        "model": model,
        "input": {
            "messages": [
                {"role": "system", "content": FIXED_INSTRUCTIONS},
                {
                    "role": "user",
                    "content": json.dumps(
                        summary, sort_keys=True, separators=(",", ":")
                    ),
                },
            ]
        },
        "parameters": {
            "result_format": "message",
            "response_format": {"type": "json_object"},
            "enable_thinking": False,
            "max_tokens": max_output_tokens,
        },
    }


def _read_limited(response: httpx.Response) -> bytes:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise QwenUnknownOutcome("QWEN_RESPONSE_OVERSIZE_UNKNOWN")
        chunks.append(chunk)
    return b"".join(chunks)


def _parse_response(payload: object, request: ModelRequest) -> ModelResponse:
    if not isinstance(payload, Mapping):
        raise QwenKnownFailure("QWEN_MALFORMED_OUTPUT")
    try:
        status_code = payload.get("status_code")
        if isinstance(status_code, bool) or status_code != 200:
            raise TypeError
        if payload.get("code") not in {None, ""}:
            raise TypeError
        output = payload["output"]
        choices = output["choices"] if isinstance(output, Mapping) else None
        choice = choices[0] if isinstance(choices, list) and len(choices) == 1 else None
        message = choice["message"] if isinstance(choice, Mapping) else None
        text = message["content"] if isinstance(message, Mapping) else None
        if (
            not isinstance(choice, Mapping)
            or choice.get("finish_reason") != "stop"
            or not isinstance(message, Mapping)
            or message.get("role") != "assistant"
            or "reasoning_content" in message
            or "tool_calls" in message
            or not isinstance(text, str)
        ):
            raise TypeError
        usage = _parse_usage(payload.get("usage"))
        candidate = PlanningCandidate.model_validate(json.loads(text))
        validated = validate_planning_candidate(
            candidate, allowed_tools=request.allowed_tools
        )
    except (
        CandidateValidationError,
        KeyError,
        TypeError,
        ValidationError,
        json.JSONDecodeError,
    ):
        raise QwenKnownFailure("QWEN_MALFORMED_OUTPUT") from None
    return ModelResponse(
        request_hash=stable_hash(request.model_dump(mode="json")),
        candidate=validated.candidate,
        candidate_hash=validated.candidate_hash,
        usage=usage,
    )


def _parse_usage(value: object) -> ModelUsage:
    if not isinstance(value, Mapping):
        raise QwenKnownFailure("QWEN_MALFORMED_OUTPUT")
    input_tokens, output_tokens = value.get("input_tokens"), value.get("output_tokens")
    if not all(
        isinstance(item, int) and not isinstance(item, bool)
        for item in (input_tokens, output_tokens)
    ):
        raise QwenKnownFailure("QWEN_MALFORMED_OUTPUT")
    try:
        return ModelUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            cost_microunits=(
                input_tokens * QWEN_INPUT_MICRO_CNY_PER_TOKEN
                + output_tokens * QWEN_OUTPUT_MICRO_CNY_PER_TOKEN
            ),
        )
    except ValidationError:
        raise QwenKnownFailure("QWEN_MALFORMED_OUTPUT") from None


__all__ = [
    "MAX_PROVIDER_OUTPUT_TOKENS",
    "MAX_RESPONSE_BYTES",
    "QWEN_ADAPTER_PROTOCOL",
    "QWEN_INPUT_MICRO_CNY_PER_TOKEN",
    "QWEN_MODEL_SNAPSHOT",
    "QWEN_OUTPUT_MICRO_CNY_PER_TOKEN",
    "QwenDashScopeAdapter",
    "QwenKnownFailure",
    "QwenUnknownOutcome",
]
