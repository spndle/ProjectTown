"""Narrow OpenAI Responses adapter for the separately-labelled Phase 1C eval.

This module is deliberately not wired into Quest execution.  Its request body
contains only a fixed instruction, a bounded structured summary, and a strict
output schema.  It never persists provider request/response material.
"""

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
from .prompt_registry import (
    FIXED_INSTRUCTIONS,
    PROMPT_REGISTRY_HASH,
    response_text_format,
)
from .provider_summary import SummaryValidationError, parse_structured_goal_summary

OPENAI_MODEL_ALIAS: Final = "gpt-5-mini"
OPENAI_MODEL_SNAPSHOT: Final = "gpt-5-mini-2025-08-07"
PRICING_VERSION: Final = "openai-2026-08-20"
FX_CNY_PER_USD: Final = 8
INPUT_MICRO_CNY_PER_TOKEN: Final = 2
OUTPUT_MICRO_CNY_PER_TOKEN: Final = 16
MAX_RESPONSE_BYTES: Final = 131_072
MAX_PROVIDER_OUTPUT_TOKENS: Final = 30_000
_SUMMARY_KEY: Final = "structured_goal_summary"
_SUMMARY_HASH_KEY: Final = "structured_goal_summary_hash"


class OpenAIKnownFailure(KnownModelFailure):
    """Stable provider-specific rejection without provider data."""

    def __init__(self, code: str) -> None:
        self.code = code
        RuntimeError.__init__(self, "OpenAI adapter request failed")


class OpenAIUnknownOutcome(UnknownModelOutcome):
    """Stable provider-specific ambiguous outcome without provider data."""

    def __init__(self, code: str) -> None:
        self.code = code
        RuntimeError.__init__(self, "OpenAI adapter outcome is unknown")


class OpenAIResponsesAdapter:
    """Synchronous, no-retry adapter with safe error surfaces."""

    def __init__(
        self,
        *,
        connection: ResolvedProviderConnection,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if (
            not isinstance(connection, ResolvedProviderConnection)
            or connection.provider != "openai"
        ):
            raise ValueError("OpenAI connection is required")
        try:
            base_url = _validate_destination(connection.provider, connection.base_url)
            _validate_api_key(connection.api_key)
            model = _validate_model_value(connection.model)
        except SecretResolutionError:
            raise ValueError("OpenAI destination is denied") from None
        if connection.destination_config_hash != _destination_hash(
            connection.provider, base_url
        ):
            raise ValueError("OpenAI connection is required")
        if connection.connection_config_hash != _connection_config_hash(
            connection.provider, base_url, model
        ):
            raise ValueError("OpenAI connection is required")
        self._api_key = connection.api_key
        self._model = model
        self._responses_url = f"{base_url}/responses"
        self._client = httpx.Client(
            timeout=httpx.Timeout(connect=5.0, read=20.0, write=5.0, pool=5.0),
            trust_env=False,
            follow_redirects=False,
            transport=transport,
        )

    def __repr__(self) -> str:
        return f"OpenAIResponsesAdapter(model={self._model})"

    def close(self) -> None:
        self._client.close()

    def create_planning_candidate(self, request: ModelRequest) -> ModelResponse:
        summary = _summary_bound_to_request(request)
        if request.max_output_tokens > MAX_PROVIDER_OUTPUT_TOKENS:
            raise OpenAIKnownFailure("OPENAI_MAX_OUTPUT_INVALID")
        body = _build_request_body(
            summary.canonical_payload(), request.max_output_tokens, self._model
        )
        try:
            with self._client.stream(
                "POST",
                self._responses_url,
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
                    raise OpenAIKnownFailure("OPENAI_BAD_REQUEST")
                if status_code in {401, 403}:
                    raise OpenAIKnownFailure("OPENAI_AUTH")
                if status_code == 429:
                    raise OpenAIKnownFailure("OPENAI_RATE_LIMIT")
                if status_code >= 500:
                    raise OpenAIUnknownOutcome("OPENAI_SERVER_UNKNOWN")
                if 300 <= status_code < 400:
                    raise OpenAIUnknownOutcome("OPENAI_REDIRECT_UNKNOWN")
                if status_code < 200 or status_code >= 300:
                    raise OpenAIKnownFailure("OPENAI_HTTP_FAILURE")
                response_bytes = _read_limited(response)
        except httpx.TimeoutException:
            raise OpenAIUnknownOutcome("OPENAI_TIMEOUT_UNKNOWN") from None
        except httpx.TransportError:
            raise OpenAIUnknownOutcome("OPENAI_TRANSPORT_UNKNOWN") from None
        try:
            payload = json.loads(response_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise OpenAIKnownFailure("OPENAI_MALFORMED_OUTPUT") from None
        return _parse_response(payload, request, self._model)


def _summary_bound_to_request(request: ModelRequest):
    parameters = request.sanitized_parameters
    if set(parameters) != {_SUMMARY_KEY, _SUMMARY_HASH_KEY}:
        raise OpenAIKnownFailure("OPENAI_SUMMARY_PARAMETERS_INVALID")
    try:
        summary = parse_structured_goal_summary(parameters[_SUMMARY_KEY])
    except SummaryValidationError:
        raise OpenAIKnownFailure("OPENAI_SUMMARY_PARAMETERS_INVALID") from None
    supplied_hash = parameters[_SUMMARY_HASH_KEY]
    if not isinstance(supplied_hash, str) or supplied_hash != summary.summary_hash:
        raise OpenAIKnownFailure("OPENAI_SUMMARY_HASH_INVALID")
    if request.input_hash != summary.summary_hash:
        raise OpenAIKnownFailure("OPENAI_INPUT_BINDING_INVALID")
    return summary


def _build_request_body(
    summary: dict[str, Any], max_output_tokens: int, model: str
) -> dict[str, Any]:
    return {
        "model": model,
        "store": False,
        "max_output_tokens": max_output_tokens,
        "instructions": FIXED_INSTRUCTIONS,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": json.dumps(
                            summary, sort_keys=True, separators=(",", ":")
                        ),
                    }
                ],
            }
        ],
        "text": {"format": response_text_format()},
    }


def _read_limited(response: httpx.Response) -> bytes:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise OpenAIUnknownOutcome("OPENAI_RESPONSE_OVERSIZE_UNKNOWN")
        chunks.append(chunk)
    return b"".join(chunks)


def _parse_response(
    payload: object, request: ModelRequest, model: str
) -> ModelResponse:
    if not isinstance(payload, Mapping):
        raise OpenAIKnownFailure("OPENAI_MALFORMED_OUTPUT")
    if payload.get("model") != model:
        raise OpenAIKnownFailure("OPENAI_MALFORMED_OUTPUT")
    status = payload.get("status")
    if status == "failed":
        raise OpenAIKnownFailure("OPENAI_PROVIDER_FAILED")
    if status in {"in_progress", "queued"}:
        raise OpenAIUnknownOutcome("OPENAI_NONTERMINAL_UNKNOWN")
    if status == "incomplete" or payload.get("incomplete_details") is not None:
        raise OpenAIKnownFailure("OPENAI_INCOMPLETE")
    if status != "completed":
        raise OpenAIKnownFailure("OPENAI_MALFORMED_OUTPUT")
    if _contains_refusal(payload):
        raise OpenAIKnownFailure("OPENAI_MALFORMED_OUTPUT")
    text = _single_output_text(payload)
    usage = _parse_usage(payload.get("usage"))
    try:
        candidate_raw = json.loads(text)
        candidate = PlanningCandidate.model_validate(candidate_raw)
        validated = validate_planning_candidate(
            candidate, allowed_tools=request.allowed_tools
        )
    except (CandidateValidationError, ValidationError, json.JSONDecodeError):
        raise OpenAIKnownFailure("OPENAI_MALFORMED_OUTPUT") from None
    return ModelResponse(
        request_hash=stable_hash(request.model_dump(mode="json")),
        candidate=validated.candidate,
        candidate_hash=validated.candidate_hash,
        usage=usage,
    )


def _contains_refusal(payload: Mapping[str, Any]) -> bool:
    output = payload.get("output")
    if not isinstance(output, list):
        return False
    for item in output:
        if not isinstance(item, Mapping):
            continue
        content = item.get("content")
        if isinstance(content, list) and any(
            isinstance(part, Mapping) and part.get("type") == "refusal"
            for part in content
        ):
            return True
    return False


def _single_output_text(payload: Mapping[str, Any]) -> str:
    output = payload.get("output")
    if not isinstance(output, list):
        raise OpenAIKnownFailure("OPENAI_MALFORMED_OUTPUT")
    texts: list[str] = []
    for item in output:
        if not isinstance(item, Mapping) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, Mapping) and part.get("type") == "output_text":
                text = part.get("text")
                if isinstance(text, str):
                    texts.append(text)
                else:
                    raise OpenAIKnownFailure("OPENAI_MALFORMED_OUTPUT")
    if len(texts) != 1:
        raise OpenAIKnownFailure("OPENAI_MALFORMED_OUTPUT")
    return texts[0]


def _parse_usage(value: object) -> ModelUsage:
    if not isinstance(value, Mapping):
        raise OpenAIKnownFailure("OPENAI_MALFORMED_OUTPUT")
    input_tokens = value.get("input_tokens")
    output_tokens = value.get("output_tokens")
    total_tokens = value.get("total_tokens")
    if not all(
        isinstance(item, int) and not isinstance(item, bool)
        for item in (input_tokens, output_tokens, total_tokens)
    ):
        raise OpenAIKnownFailure("OPENAI_MALFORMED_OUTPUT")
    try:
        return ModelUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost_microunits=(input_tokens * INPUT_MICRO_CNY_PER_TOKEN)
            + (output_tokens * OUTPUT_MICRO_CNY_PER_TOKEN),
        )
    except ValidationError:
        raise OpenAIKnownFailure("OPENAI_MALFORMED_OUTPUT") from None


def safe_audit_metadata(model: str) -> dict[str, str | int]:
    """Return non-secret, non-prompt identifiers suitable for later audit code."""

    return {
        "provider": "openai",
        "model_alias": OPENAI_MODEL_ALIAS,
        "model_snapshot": _validate_model_value(model),
        "pricing_version": PRICING_VERSION,
        "prompt_registry_hash": PROMPT_REGISTRY_HASH,
        "fx_cny_per_usd": FX_CNY_PER_USD,
    }


__all__ = [
    "FX_CNY_PER_USD",
    "INPUT_MICRO_CNY_PER_TOKEN",
    "MAX_PROVIDER_OUTPUT_TOKENS",
    "MAX_RESPONSE_BYTES",
    "OPENAI_MODEL_ALIAS",
    "OPENAI_MODEL_SNAPSHOT",
    "OUTPUT_MICRO_CNY_PER_TOKEN",
    "PRICING_VERSION",
    "OpenAIKnownFailure",
    "OpenAIResponsesAdapter",
    "OpenAIUnknownOutcome",
    "safe_audit_metadata",
]
