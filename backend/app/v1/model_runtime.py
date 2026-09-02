"""Unwired Phase 1A coordinator for auditable model planning candidates."""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    field_validator,
    model_validator,
)

from ..runtime import stable_hash
from ..telemetry import NoOpTelemetry, Telemetry
from .model_adapter import (
    CandidateValidationError,
    KnownModelFailure,
    ModelAdapter,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    UnknownModelOutcome,
    validate_planning_candidate,
)
from .storage import V1Storage

_IDENTIFIER_PATTERN = "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$"
_LABEL_PATTERN = "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$"
_SENSITIVE_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
)
PUBLIC_TOKEN_COUNTER_FIELDS = frozenset(
    {
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "reserved_tokens",
        "settled_tokens",
        "max_tokens",
        "estimated_input_tokens",
        "max_output_tokens",
    }
)
_MODEL_CALL_LOCK = threading.RLock()


class _RuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ModelCallSubmission(_RuntimeModel):
    quest_id: str = Field(min_length=1, max_length=120, pattern=_IDENTIFIER_PATTERN)
    idempotency_key: str = Field(
        min_length=1, max_length=120, pattern=_IDENTIFIER_PATTERN
    )
    prompt_version: str = Field(
        min_length=1, max_length=120, pattern=_IDENTIFIER_PATTERN
    )
    input_payload: dict[str, JsonValue] = Field(min_length=1, max_length=32)
    allowed_tools: list[str] = Field(min_length=1, max_length=64)
    sanitized_parameters: dict[str, JsonValue] = Field(
        default_factory=dict, max_length=32
    )
    reserved_tokens: int = Field(ge=1, le=32_768)
    max_output_tokens: int = Field(ge=1, le=32_768)
    expected_state_version: int = Field(ge=1, le=1_000_000_000)
    adapter_label: str = Field(min_length=1, max_length=120, pattern=_LABEL_PATTERN)
    model_label: str = Field(min_length=1, max_length=120, pattern=_LABEL_PATTERN)
    # Absent means the established offline/default path.  The isolated Phase 1C
    # runner supplies the storage-authorized quote explicitly.
    cost_reservation: dict[str, JsonValue] | None = None
    retain_candidate: bool = True

    @field_validator("input_payload", "sanitized_parameters")
    @classmethod
    def reject_sensitive_values(
        cls, value: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        _validate_public_json(value)
        return value

    @model_validator(mode="after")
    def validate_reservation(self) -> ModelCallSubmission:
        if self.reserved_tokens < self.max_output_tokens:
            raise ValueError("reserved_tokens must cover max_output_tokens")
        if self.cost_reservation is not None:
            _validate_public_json(self.cost_reservation)
        return self


class ModelCallResult(_RuntimeModel):
    call_id: str | None = Field(default=None, max_length=120)
    attempt_id: str | None = Field(default=None, max_length=160)
    outcome: Literal[
        "validated_current",
        "stale",
        "failed",
        "unknown_outcome",
        "invalid",
        "budget_rejected",
        "in_progress",
    ]
    validation_status: Literal[
        "validated_current",
        "stale",
        "invalid",
        "pending",
        "conflict",
        "cancelled_before_dispatch",
    ]
    candidate_hash: str | None = Field(default=None, pattern="^[0-9a-f]{64}$")
    response_hash: str | None = Field(default=None, pattern="^[0-9a-f]{64}$")
    error_code: str | None = Field(default=None, max_length=80, pattern=_LABEL_PATTERN)
    idempotent_replay: bool = False


class ModelCallCoordinator:
    """Reserve, dispatch, validate, and settle a candidate without adopting it."""

    def __init__(
        self,
        storage: V1Storage,
        adapter: ModelAdapter,
        *,
        telemetry: Telemetry | None = None,
    ) -> None:
        self._storage = storage
        self._adapter = adapter
        self._telemetry = telemetry or NoOpTelemetry()

    def run(
        self,
        *,
        quest_id: str,
        idempotency_key: str,
        prompt_version: str,
        input_payload: Mapping[str, JsonValue],
        allowed_tools: list[str],
        sanitized_parameters: Mapping[str, JsonValue],
        reserved_tokens: int,
        max_output_tokens: int,
        expected_state_version: int,
        adapter_label: str,
        model_label: str,
        cost_reservation: Mapping[str, JsonValue] | None = None,
        retain_candidate: bool = True,
    ) -> ModelCallResult:
        """Execute one auditable candidate request with no automatic retry."""

        started = time.perf_counter()
        result = self._run(
            quest_id=quest_id,
            idempotency_key=idempotency_key,
            prompt_version=prompt_version,
            input_payload=input_payload,
            allowed_tools=allowed_tools,
            sanitized_parameters=sanitized_parameters,
            reserved_tokens=reserved_tokens,
            max_output_tokens=max_output_tokens,
            expected_state_version=expected_state_version,
            adapter_label=adapter_label,
            model_label=model_label,
            cost_reservation=cost_reservation,
            retain_candidate=retain_candidate,
        )
        if self._telemetry.enabled:
            try:
                self._emit_telemetry(
                    quest_id=quest_id,
                    adapter_label=adapter_label,
                    model_label=model_label,
                    prompt_version=prompt_version,
                    reserved_tokens=reserved_tokens,
                    max_output_tokens=max_output_tokens,
                    result=result,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            except Exception:  # noqa: BLE001 - derived telemetry reads are never business work.
                return result
        return result

    def _run(
        self,
        *,
        quest_id: str,
        idempotency_key: str,
        prompt_version: str,
        input_payload: Mapping[str, JsonValue],
        allowed_tools: list[str],
        sanitized_parameters: Mapping[str, JsonValue],
        reserved_tokens: int,
        max_output_tokens: int,
        expected_state_version: int,
        adapter_label: str,
        model_label: str,
        cost_reservation: Mapping[str, JsonValue] | None,
        retain_candidate: bool,
    ) -> ModelCallResult:

        try:
            submission = ModelCallSubmission(
                quest_id=quest_id,
                idempotency_key=idempotency_key,
                prompt_version=prompt_version,
                input_payload=dict(input_payload),
                allowed_tools=list(allowed_tools),
                sanitized_parameters=dict(sanitized_parameters),
                reserved_tokens=reserved_tokens,
                max_output_tokens=max_output_tokens,
                expected_state_version=expected_state_version,
                adapter_label=adapter_label,
                model_label=model_label,
                cost_reservation=(
                    dict(cost_reservation) if cost_reservation is not None else None
                ),
                retain_candidate=retain_candidate,
            )
            request = self._request_for_submission(submission)
        except (TypeError, ValidationError, ValueError):
            return self._result(
                outcome="invalid",
                validation_status="invalid",
                error_code="INVALID_REQUEST",
            )

        call_id = _call_id(submission.quest_id, submission.idempotency_key)
        with _MODEL_CALL_LOCK:
            existing = self._storage.get_model_call(call_id)
            if existing is not None:
                try:
                    matches = self._matches_existing(existing, submission, request)
                    replay = self._result_from_record(existing, idempotent_replay=True)
                except (TypeError, ValueError):
                    return self._result(
                        call_id=call_id,
                        outcome="invalid",
                        validation_status="invalid",
                        error_code="DURABLE_RECORD_INVALID",
                    )
                if not matches:
                    return self._result(
                        call_id=call_id,
                        outcome="invalid",
                        validation_status="conflict",
                        error_code="IDEMPOTENCY_CONFLICT",
                    )
                return replay

            audit_parameters = {
                "schema_version": 1,
                "allowed_tools": request.allowed_tools,
                "max_tokens": request.max_output_tokens,
                "sanitized_parameters": request.sanitized_parameters,
            }
            if submission.cost_reservation is not None:
                # Storage owns the full immutable quote in migration 6.  Keep
                # only its hash in legacy model parameters: those parameters
                # intentionally reject token-bearing arbitrary objects.
                audit_parameters["cost_reservation_hash"] = stable_hash(
                    submission.cost_reservation
                )
                audit_parameters["candidate_retention"] = (
                    "hash_only" if not submission.retain_candidate else "full"
                )
            try:
                prepared = self._storage.prepare_model_call(
                    call_id=call_id,
                    quest_id=submission.quest_id,
                    purpose=request.purpose,
                    idempotency_key=submission.idempotency_key,
                    input_hash=request.input_hash,
                    prompt_version=request.prompt_version,
                    request_schema_version=request.schema_version,
                    candidate_schema_version=1,
                    adapter=submission.adapter_label,
                    model=submission.model_label,
                    parameters=audit_parameters,
                    reserved_tokens=submission.reserved_tokens,
                    expected_state_version=submission.expected_state_version,
                    dispatch_token=_dispatch_token(call_id),
                    cost_reservation=submission.cost_reservation,
                )
            except ValueError as error:
                if str(error) in {
                    "model token budget exceeded",
                    "model cost per-call budget exceeded",
                    "model cost total budget exceeded",
                    "model cost account is breached",
                }:
                    return self._result(
                        call_id=call_id,
                        outcome="budget_rejected",
                        validation_status="invalid",
                        error_code="BUDGET_REJECTED",
                    )
                return self._result(
                    call_id=call_id,
                    outcome="invalid",
                    validation_status="invalid",
                    error_code="PREPARE_REJECTED",
                )
            except (KeyError, RuntimeError):
                return self._result(
                    call_id=call_id,
                    outcome="invalid",
                    validation_status="invalid",
                    error_code="PREPARE_REJECTED",
                )

            attempt = _attempt_for_record(prepared)
            if attempt["status"] != "prepared":
                return self._result_from_record(prepared, idempotent_replay=True)
            attempt_id = str(attempt["attempt_id"])
            try:
                request = self._request_from_durable_binding(
                    dict(prepared["call"]), attempt
                )
            except (TypeError, ValidationError, ValueError):
                return self._record_failure(
                    attempt_id,
                    "DURABLE_PARAMETERS_INVALID",
                    usage=None,
                    reservation=int(attempt["reserved_tokens"]),
                    conservative=False,
                )
            try:
                self._storage.mark_model_attempt_dispatched(attempt_id)
            except (KeyError, ValueError, RuntimeError):
                return self._result_from_record(
                    self._storage.get_model_call(call_id) or prepared,
                    idempotent_replay=True,
                )

            try:
                raw_response = self._adapter.create_planning_candidate(request)
            except KnownModelFailure as error:
                return self._record_failure(
                    attempt_id,
                    _safe_adapter_code(error, "KNOWN_FAILURE"),
                    usage=None,
                    reservation=submission.reserved_tokens,
                    conservative=False,
                )
            except UnknownModelOutcome as error:
                return self._mark_unknown(
                    attempt_id, _safe_adapter_code(error, "UNKNOWN_OUTCOME")
                )
            except Exception:  # noqa: BLE001 - dispatched outcomes must remain reconcilable.
                return self._mark_unknown(attempt_id, "UNKNOWN_OUTCOME")

            usage = _extract_valid_usage(raw_response)
            try:
                response = _parse_response(raw_response)
                request_hash = stable_hash(request.model_dump(mode="json"))
                if response.request_hash != request_hash:
                    return self._record_failure(
                        attempt_id,
                        "REQUEST_HASH_MISMATCH",
                        usage=usage,
                        reservation=submission.reserved_tokens,
                    )
                candidate = validate_planning_candidate(
                    response.candidate, allowed_tools=request.allowed_tools
                )
                if candidate.candidate_hash != response.candidate_hash:
                    return self._record_failure(
                        attempt_id,
                        "CANDIDATE_HASH_MISMATCH",
                        usage=usage,
                        reservation=submission.reserved_tokens,
                    )
            except CandidateValidationError:
                return self._record_failure(
                    attempt_id,
                    "CANDIDATE_REJECTED",
                    usage=usage,
                    reservation=submission.reserved_tokens,
                )
            except (TypeError, ValidationError, ValueError):
                return self._record_failure(
                    attempt_id,
                    "INVALID_RESPONSE",
                    usage=usage,
                    reservation=submission.reserved_tokens,
                )

            try:
                settled = self._storage.record_model_success(
                    attempt_id,
                    submission.quest_id,
                    request.input_hash,
                    stable_hash(response.model_dump(mode="json")),
                    candidate.candidate.model_dump(mode="json"),
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    usage=response.usage.model_dump(mode="json"),
                    cost={"cost_microunits": response.usage.cost_microunits},
                    retain_candidate=submission.retain_candidate,
                )
            except (KeyError, ValueError, RuntimeError):
                return self._mark_unknown(attempt_id, "SETTLEMENT_UNKNOWN")
            return self._result_from_attempt(settled)

    def _emit_telemetry(
        self,
        *,
        quest_id: str,
        adapter_label: str,
        model_label: str,
        prompt_version: str,
        reserved_tokens: int,
        max_output_tokens: int,
        result: ModelCallResult,
        duration_ms: int,
    ) -> None:
        usage: Mapping[str, int] | None = None
        if result.call_id is not None:
            try:
                record = self._storage.get_model_call(result.call_id)
                if record is not None:
                    attempt = _attempt_for_record(record)
                    stored_usage = attempt.get("usage")
                    if isinstance(stored_usage, Mapping):
                        usage = {
                            key: value
                            for key, value in stored_usage.items()
                            if isinstance(key, str)
                            and isinstance(value, int)
                            and not isinstance(value, bool)
                        }
            except (KeyError, TypeError, ValueError, RuntimeError):
                usage = None
        self._telemetry.emit_model_call(
            quest_id=quest_id,
            adapter=adapter_label,
            model=model_label,
            prompt_version=prompt_version,
            call_id=result.call_id,
            attempt_id=result.attempt_id,
            outcome=result.outcome,
            validation_status=result.validation_status,
            error_type=result.error_code,
            idempotent_replay=result.idempotent_replay,
            reserved_tokens=reserved_tokens,
            max_output_tokens=max_output_tokens,
            usage=usage,
            duration_ms=duration_ms,
        )

    def _request_for_submission(self, submission: ModelCallSubmission) -> ModelRequest:
        input_hash = stable_hash(submission.input_payload)
        return ModelRequest(
            quest_id=submission.quest_id,
            purpose="planning",
            prompt_version=submission.prompt_version,
            input_hash=input_hash,
            contract_id="pending",
            contract_version=1,
            contract_hash="0" * 64,
            plan_id="pending",
            plan_version=1,
            plan_hash="0" * 64,
            expected_state_version=submission.expected_state_version,
            allowed_tools=submission.allowed_tools,
            max_output_tokens=submission.max_output_tokens,
            sanitized_parameters=submission.sanitized_parameters,
        )

    def _request_from_durable_binding(
        self, call: Mapping[str, Any], attempt: Mapping[str, Any]
    ) -> ModelRequest:
        parameters = _durable_parameters(attempt)
        return ModelRequest(
            quest_id=str(call["quest_id"]),
            purpose="planning",
            prompt_version=str(call["prompt_version"]),
            input_hash=str(call["input_hash"]),
            contract_id=str(call["contract_id"]),
            contract_version=int(call["contract_version"]),
            contract_hash=str(call["contract_hash"]),
            plan_id=str(call["plan_id"]),
            plan_version=int(call["plan_version"]),
            plan_hash=str(call["plan_hash"]),
            expected_state_version=int(call["expected_state_version"]),
            allowed_tools=parameters["allowed_tools"],
            max_output_tokens=parameters["max_tokens"],
            sanitized_parameters=parameters["sanitized_parameters"],
        )

    def _matches_existing(
        self,
        record: Mapping[str, Any],
        submission: ModelCallSubmission,
        request: ModelRequest,
    ) -> bool:
        call = dict(record["call"])
        attempt = _attempt_for_record(record)
        audit_parameters: dict[str, Any] = {
            "schema_version": 1,
            "allowed_tools": request.allowed_tools,
            "max_tokens": request.max_output_tokens,
            "sanitized_parameters": request.sanitized_parameters,
        }
        if submission.cost_reservation is not None:
            audit_parameters["cost_reservation_hash"] = stable_hash(
                submission.cost_reservation
            )
            audit_parameters["candidate_retention"] = (
                "hash_only" if not submission.retain_candidate else "full"
            )
        return (
            call["quest_id"] == submission.quest_id
            and call["purpose"] == "planning"
            and call["idempotency_key"] == submission.idempotency_key
            and call["input_hash"] == request.input_hash
            and call["prompt_version"] == submission.prompt_version
            and int(call["request_schema_version"]) == 1
            and int(call["candidate_schema_version"]) == 1
            and int(call["expected_state_version"]) == submission.expected_state_version
            and attempt["adapter"] == submission.adapter_label
            and attempt["model"] == submission.model_label
            and int(attempt["reserved_tokens"]) == submission.reserved_tokens
            and attempt["parameters"] == audit_parameters
        )

    def _record_failure(
        self,
        attempt_id: str,
        code: str,
        *,
        usage: ModelUsage | None,
        reservation: int,
        conservative: bool = True,
    ) -> ModelCallResult:
        settled_usage = usage or ModelUsage(
            input_tokens=reservation if conservative else 0,
            output_tokens=0,
            total_tokens=reservation if conservative else 0,
            cost_microunits=0,
        )
        try:
            attempt = self._storage.record_model_failure(
                attempt_id,
                {"code": code},
                input_tokens=settled_usage.input_tokens,
                output_tokens=settled_usage.output_tokens,
                usage=settled_usage.model_dump(mode="json"),
                cost={"cost_microunits": settled_usage.cost_microunits},
            )
        except (KeyError, ValueError, RuntimeError):
            return self._mark_unknown(attempt_id, "SETTLEMENT_UNKNOWN")
        return self._result_from_attempt(attempt, error_code=code)

    def _mark_unknown(self, attempt_id: str, code: str) -> ModelCallResult:
        try:
            attempt = self._storage.mark_model_attempt_unknown(
                attempt_id, {"code": code}
            )
        except (KeyError, ValueError, RuntimeError):
            return self._result(
                attempt_id=attempt_id,
                outcome="unknown_outcome",
                validation_status="pending",
                error_code="UNKNOWN_OUTCOME",
            )
        return self._result_from_attempt(attempt, error_code=code)

    @staticmethod
    def _result_from_record(
        record: Mapping[str, Any], *, idempotent_replay: bool
    ) -> ModelCallResult:
        return ModelCallCoordinator._result_from_attempt(
            _attempt_for_record(record), idempotent_replay=idempotent_replay
        )

    @staticmethod
    def _result_from_attempt(
        attempt: Mapping[str, Any],
        *,
        error_code: str | None = None,
        idempotent_replay: bool = False,
    ) -> ModelCallResult:
        status = str(attempt["status"])
        validation = str(attempt["validation_status"])
        if validation == "validated_current":
            outcome = "validated_current"
        elif validation == "stale":
            outcome = "stale"
        elif status == "unknown_outcome":
            outcome = "unknown_outcome"
        elif status in {"prepared", "dispatched"}:
            outcome = "in_progress"
        else:
            outcome = "failed"
        stored_error = attempt.get("error")
        if error_code is None and isinstance(stored_error, Mapping):
            candidate_code = stored_error.get("code")
            error_code = (
                str(candidate_code) if isinstance(candidate_code, str) else None
            )
        return ModelCallResult(
            call_id=str(attempt["call_id"]),
            attempt_id=str(attempt["attempt_id"]),
            outcome=outcome,
            validation_status=validation,
            candidate_hash=attempt.get("candidate_hash"),
            response_hash=attempt.get("response_hash"),
            error_code=error_code,
            idempotent_replay=idempotent_replay,
        )

    @staticmethod
    def _result(
        *,
        outcome: Literal[
            "validated_current",
            "stale",
            "failed",
            "unknown_outcome",
            "invalid",
            "budget_rejected",
            "in_progress",
        ],
        validation_status: Literal[
            "validated_current",
            "stale",
            "invalid",
            "pending",
            "conflict",
            "cancelled_before_dispatch",
        ],
        call_id: str | None = None,
        attempt_id: str | None = None,
        error_code: str | None = None,
    ) -> ModelCallResult:
        return ModelCallResult(
            call_id=call_id,
            attempt_id=attempt_id,
            outcome=outcome,
            validation_status=validation_status,
            error_code=error_code,
        )


def _call_id(quest_id: str, idempotency_key: str) -> str:
    return f"modelcall_{stable_hash({'quest_id': quest_id, 'idempotency_key': idempotency_key})[:24]}"


def _dispatch_token(call_id: str) -> str:
    return f"dispatch_{call_id}_1"


def _attempt_for_record(record: Mapping[str, Any]) -> dict[str, Any]:
    attempts = [dict(item) for item in record.get("attempts", [])]
    if not attempts:
        raise ValueError("model call record has no attempts")
    winner = record.get("call", {}).get("winning_attempt_id")
    if isinstance(winner, str):
        for attempt in attempts:
            if attempt.get("attempt_id") == winner:
                return attempt
        raise ValueError("model call winner is missing")
    return max(attempts, key=lambda attempt: int(attempt["attempt_no"]))


def _durable_parameters(attempt: Mapping[str, Any]) -> dict[str, Any]:
    parameters = attempt.get("parameters")
    required = {
        "schema_version",
        "allowed_tools",
        "max_tokens",
        "sanitized_parameters",
    }
    allowed = required | {"cost_reservation_hash", "candidate_retention"}
    if (
        not isinstance(parameters, Mapping)
        or not required <= set(parameters)
        or not set(parameters) <= allowed
    ):
        raise ValueError("durable model parameters are invalid")
    schema_version = parameters["schema_version"]
    allowed_tools = parameters["allowed_tools"]
    max_tokens = parameters["max_tokens"]
    sanitized_parameters = parameters["sanitized_parameters"]
    if (
        schema_version != 1
        or not isinstance(allowed_tools, list)
        or not all(isinstance(tool, str) for tool in allowed_tools)
        or isinstance(max_tokens, bool)
        or not isinstance(max_tokens, int)
        or not isinstance(sanitized_parameters, Mapping)
    ):
        raise ValueError("durable model parameters are invalid")
    result = {
        "allowed_tools": list(allowed_tools),
        "max_tokens": max_tokens,
        "sanitized_parameters": dict(sanitized_parameters),
    }
    if "cost_reservation_hash" in parameters:
        cost_reservation_hash = parameters["cost_reservation_hash"]
        candidate_retention = parameters.get("candidate_retention")
        if (
            not isinstance(cost_reservation_hash, str)
            or not re.fullmatch("[0-9a-f]{64}", cost_reservation_hash)
            or candidate_retention not in {"full", "hash_only"}
        ):
            raise ValueError("durable model parameters are invalid")
        result["cost_reservation_hash"] = cost_reservation_hash
        result["candidate_retention"] = candidate_retention
    return result


def _safe_adapter_code(error: BaseException, fallback: str) -> str:
    code = getattr(error, "code", fallback)
    if isinstance(code, str) and re.fullmatch(_LABEL_PATTERN, code):
        return code
    return fallback


def _parse_response(value: Any) -> ModelResponse:
    if isinstance(value, ModelResponse):
        return ModelResponse.model_validate(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        return ModelResponse.model_validate(value)
    raise TypeError("model response must be a mapping")


def _extract_valid_usage(value: Any) -> ModelUsage | None:
    raw_usage = (
        value.usage
        if isinstance(value, ModelResponse)
        else (value.get("usage") if isinstance(value, Mapping) else None)
    )
    if isinstance(raw_usage, ModelUsage):
        return raw_usage
    if isinstance(raw_usage, Mapping):
        try:
            return ModelUsage.model_validate(raw_usage)
        except ValidationError:
            return None
    return None


def _validate_public_json(value: JsonValue, *, depth: int = 0) -> None:
    if depth > 4:
        raise ValueError("JSON value exceeds the supported nesting depth")
    if isinstance(value, str):
        if len(value) > 8_000:
            raise ValueError("JSON string exceeds the supported size")
    elif isinstance(value, dict):
        if len(value) > 32:
            raise ValueError("JSON object exceeds the supported size")
        for key, child in value.items():
            normalized = key.casefold().replace("-", "_")
            if len(key) > 120:
                raise ValueError("model call parameters must be public")
            if "token" in normalized:
                if (
                    normalized not in PUBLIC_TOKEN_COUNTER_FIELDS
                    or isinstance(child, bool)
                    or not isinstance(child, int)
                    or child < 0
                ):
                    raise ValueError("model call parameters must be public")
            elif any(marker in normalized for marker in _SENSITIVE_MARKERS):
                raise ValueError("model call parameters must be public")
            _validate_public_json(child, depth=depth + 1)
    elif isinstance(value, list):
        if len(value) > 64:
            raise ValueError("JSON array exceeds the supported size")
        for child in value:
            _validate_public_json(child, depth=depth + 1)


__all__ = [
    "PUBLIC_TOKEN_COUNTER_FIELDS",
    "ModelCallCoordinator",
    "ModelCallResult",
    "ModelCallSubmission",
]
