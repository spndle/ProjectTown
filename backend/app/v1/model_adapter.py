"""Strict, provider-neutral contracts for optional model planning candidates.

This module is intentionally isolated from execution and persistence concerns.
Candidates are data only: callers must validate and explicitly adopt them in a
separate workflow before any action can occur.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Literal, Protocol, runtime_checkable

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

SCHEMA_VERSION = 1
MAX_CANDIDATE_BYTES = 32_768
_HASH_PATTERN = "^[0-9a-f]{64}$"
_IDENTIFIER_PATTERN = "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$"
_TOOL_NAME_PATTERN = "^[A-Za-z][A-Za-z0-9_.-]{0,119}$"
_SENSITIVE_PARAMETER_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "prompt",
    "secret",
)


class _ContractModel(BaseModel):
    """Strict JSON-compatible base for the Phase 1A contract."""

    model_config = ConfigDict(extra="forbid", strict=True)


class ModelRequest(_ContractModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    quest_id: str = Field(min_length=1, max_length=120, pattern=_IDENTIFIER_PATTERN)
    purpose: Literal["planning"]
    prompt_version: str = Field(
        min_length=1, max_length=120, pattern=_IDENTIFIER_PATTERN
    )
    input_hash: str = Field(pattern=_HASH_PATTERN)
    contract_id: str = Field(min_length=1, max_length=120, pattern=_IDENTIFIER_PATTERN)
    contract_version: int = Field(ge=1, le=1_000_000)
    contract_hash: str = Field(pattern=_HASH_PATTERN)
    plan_id: str = Field(min_length=1, max_length=120, pattern=_IDENTIFIER_PATTERN)
    plan_version: int = Field(ge=1, le=1_000_000)
    plan_hash: str = Field(pattern=_HASH_PATTERN)
    expected_state_version: int = Field(ge=1, le=1_000_000_000)
    allowed_tools: list[str] = Field(min_length=1, max_length=64)
    max_output_tokens: int = Field(ge=1, le=32_768)
    sanitized_parameters: dict[str, JsonValue] = Field(
        default_factory=dict, max_length=32
    )

    @field_validator("allowed_tools")
    @classmethod
    def validate_allowed_tools(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("allowed_tools must be unique")
        if any(not re.fullmatch(_TOOL_NAME_PATTERN, item) for item in value):
            raise ValueError("allowed_tools contains an invalid name")
        return value

    @field_validator("sanitized_parameters")
    @classmethod
    def validate_sanitized_parameters(
        cls, value: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        _validate_bounded_json(value, reject_sensitive_keys=True)
        return value


class PlanningStepCandidate(_ContractModel):
    id: str = Field(min_length=1, max_length=120, pattern=_IDENTIFIER_PATTERN)
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=2_000)
    tool_name: str = Field(min_length=1, max_length=120, pattern=_TOOL_NAME_PATTERN)
    tool_args: dict[str, JsonValue] = Field(default_factory=dict, max_length=32)
    dependencies: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("dependencies")
    @classmethod
    def validate_dependencies(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("dependencies must be unique")
        if any(not re.fullmatch(_IDENTIFIER_PATTERN, item) for item in value):
            raise ValueError("dependencies contains an invalid id")
        return value

    @field_validator("tool_args")
    @classmethod
    def validate_tool_args(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        _validate_bounded_json(value, reject_sensitive_keys=True)
        return value


class PlanningCandidate(_ContractModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    id: str = Field(min_length=1, max_length=120, pattern=_IDENTIFIER_PATTERN)
    version: int = Field(ge=1, le=1_000_000)
    summary: str = Field(min_length=1, max_length=2_000)
    steps: list[PlanningStepCandidate] = Field(min_length=1, max_length=64)


class ModelUsage(_ContractModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    input_tokens: int = Field(ge=0, le=32_768)
    output_tokens: int = Field(ge=0, le=32_768)
    total_tokens: int = Field(ge=0, le=65_536)
    cost_microunits: int = Field(ge=0, le=1_000_000_000)

    @model_validator(mode="after")
    def validate_total_tokens(self) -> ModelUsage:
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("total_tokens must equal input_tokens plus output_tokens")
        return self


class ModelResponse(_ContractModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    request_hash: str = Field(pattern=_HASH_PATTERN)
    candidate: PlanningCandidate
    candidate_hash: str = Field(pattern=_HASH_PATTERN)
    usage: ModelUsage

    @model_validator(mode="after")
    def validate_candidate_hash(self) -> ModelResponse:
        if self.candidate_hash != stable_hash(self.candidate.model_dump(mode="json")):
            raise ValueError("candidate_hash does not match candidate")
        return self


class CandidateValidationError(ValueError):
    """A stable, safe rejection for an untrusted planning candidate."""

    def __init__(self, code: str):
        self.code = code
        super().__init__("planning candidate rejected")


class ValidatedPlanningCandidate(_ContractModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    candidate: PlanningCandidate
    candidate_hash: str = Field(pattern=_HASH_PATTERN)

    @model_validator(mode="after")
    def validate_hash(self) -> ValidatedPlanningCandidate:
        if self.candidate_hash != stable_hash(self.candidate.model_dump(mode="json")):
            raise ValueError("candidate_hash does not match candidate")
        return self


class ModelAdapterError(RuntimeError):
    """Safe adapter failure with no original provider error attached."""

    code: str

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class KnownModelFailure(ModelAdapterError):
    def __init__(self) -> None:
        super().__init__("KNOWN_FAILURE", "model adapter request failed")


class UnknownModelOutcome(ModelAdapterError):
    def __init__(self) -> None:
        super().__init__("UNKNOWN_OUTCOME", "model adapter outcome is unknown")


@runtime_checkable
class ModelAdapter(Protocol):
    """Provider-neutral planning interface; it accepts and returns only contracts."""

    def create_planning_candidate(self, request: ModelRequest) -> ModelResponse:
        """Return an untrusted planning candidate or a safe adapter error."""


class DeterministicFakeModelAdapter:
    """Offline adapter for deterministic tests and local contract validation."""

    def __init__(
        self,
        *,
        known_failure_input_hashes: Sequence[str] = (),
        unknown_outcome_input_hashes: Sequence[str] = (),
    ) -> None:
        self._known_failure_input_hashes = frozenset(known_failure_input_hashes)
        self._unknown_outcome_input_hashes = frozenset(unknown_outcome_input_hashes)
        self._call_count = 0

    @property
    def call_count(self) -> int:
        return self._call_count

    def create_planning_candidate(self, request: ModelRequest) -> ModelResponse:
        self._call_count += 1
        if request.input_hash in self._known_failure_input_hashes:
            raise KnownModelFailure()
        if request.input_hash in self._unknown_outcome_input_hashes:
            raise UnknownModelOutcome()

        request_payload = request.model_dump(mode="json")
        request_hash = stable_hash(request_payload)
        step_suffix = stable_hash(
            {"input_hash": request.input_hash, "request": request_hash}
        )[:12]
        candidate = PlanningCandidate(
            id=f"candidate_{step_suffix}",
            version=1,
            summary="Deterministic offline planning candidate.",
            steps=[
                PlanningStepCandidate(
                    id=f"step_{step_suffix}",
                    title="Prepare planning artifact",
                    description="A deterministic, non-executing planning step.",
                    tool_name=request.allowed_tools[0],
                    tool_args={
                        "path": f"deliverables/plan_{request.input_hash[:12]}.json"
                    },
                )
            ],
        )
        validated = validate_planning_candidate(
            candidate, allowed_tools=request.allowed_tools
        )
        output_tokens = min(16, request.max_output_tokens)
        return ModelResponse(
            request_hash=request_hash,
            candidate=validated.candidate,
            candidate_hash=validated.candidate_hash,
            usage=ModelUsage(
                input_tokens=0,
                output_tokens=output_tokens,
                total_tokens=output_tokens,
                cost_microunits=0,
            ),
        )


def validate_planning_candidate(
    candidate: PlanningCandidate | Mapping[str, Any],
    *,
    allowed_tools: Sequence[str],
    max_candidate_bytes: int = MAX_CANDIDATE_BYTES,
) -> ValidatedPlanningCandidate:
    """Validate untrusted planning data without invoking an action."""

    if not 1 <= max_candidate_bytes <= MAX_CANDIDATE_BYTES:
        raise ValueError("max_candidate_bytes must be within the supported range")
    parsed = _parse_candidate(candidate)
    canonical = parsed.model_dump(mode="json")
    if len(_canonical_json_bytes(canonical)) > max_candidate_bytes:
        raise CandidateValidationError("CANDIDATE_TOO_LARGE")

    allowed = set(allowed_tools)
    step_ids = [step.id for step in parsed.steps]
    if len(set(step_ids)) != len(step_ids):
        raise CandidateValidationError("DUPLICATE_STEP_ID")
    for step in parsed.steps:
        if step.tool_name not in allowed:
            raise CandidateValidationError("TOOL_NOT_ALLOWED")
        _validate_relative_paths(step.tool_args)
        for dependency in step.dependencies:
            if dependency not in step_ids:
                raise CandidateValidationError("MISSING_DEPENDENCY")
    if _has_dependency_cycle(parsed.steps):
        raise CandidateValidationError("DEPENDENCY_CYCLE")

    return ValidatedPlanningCandidate(
        candidate=parsed,
        candidate_hash=stable_hash(canonical),
    )


def _parse_candidate(
    candidate: PlanningCandidate | Mapping[str, Any],
) -> PlanningCandidate:
    if isinstance(candidate, PlanningCandidate):
        if candidate.schema_version != SCHEMA_VERSION:
            raise CandidateValidationError("UNSUPPORTED_SCHEMA_VERSION")
        return candidate
    if not isinstance(candidate, Mapping):
        raise CandidateValidationError("INVALID_CANDIDATE")
    if candidate.get("schema_version") != SCHEMA_VERSION:
        raise CandidateValidationError("UNSUPPORTED_SCHEMA_VERSION")
    try:
        return PlanningCandidate.model_validate(candidate)
    except ValidationError:
        raise CandidateValidationError("INVALID_CANDIDATE") from None


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    import json

    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _validate_bounded_json(
    value: JsonValue, *, reject_sensitive_keys: bool = False, depth: int = 0
) -> None:
    if depth > 4:
        raise ValueError("JSON value exceeds the supported nesting depth")
    if isinstance(value, str):
        if len(value) > 8_000:
            raise ValueError("JSON string exceeds the supported size")
    elif isinstance(value, dict):
        if len(value) > 32:
            raise ValueError("JSON object exceeds the supported size")
        for key, child in value.items():
            if len(key) > 120:
                raise ValueError("JSON key exceeds the supported size")
            if reject_sensitive_keys and any(
                marker in key.lower() for marker in _SENSITIVE_PARAMETER_MARKERS
            ):
                raise ValueError("JSON object contains a prohibited key")
            _validate_bounded_json(
                child, reject_sensitive_keys=reject_sensitive_keys, depth=depth + 1
            )
    elif isinstance(value, list):
        if len(value) > 64:
            raise ValueError("JSON array exceeds the supported size")
        for child in value:
            _validate_bounded_json(
                child, reject_sensitive_keys=reject_sensitive_keys, depth=depth + 1
            )


def _validate_relative_paths(value: JsonValue, key: str = "") -> None:
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            _validate_relative_paths(child_value, child_key)
    elif isinstance(value, list):
        for item in value:
            _validate_relative_paths(item, key)
    elif key.lower().endswith("path") and isinstance(value, str):
        normalized = value.replace("\\", "/")
        if (
            not normalized
            or normalized.startswith(("/", "//"))
            or PurePosixPath(normalized).is_absolute()
            or PureWindowsPath(value).is_absolute()
            or ".." in PurePosixPath(normalized).parts
        ):
            raise CandidateValidationError("UNSAFE_PATH")


def _has_dependency_cycle(steps: Sequence[PlanningStepCandidate]) -> bool:
    dependencies = {step.id: tuple(step.dependencies) for step in steps}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(step_id: str) -> bool:
        if step_id in visiting:
            return True
        if step_id in visited:
            return False
        visiting.add(step_id)
        for dependency in dependencies[step_id]:
            if visit(dependency):
                return True
        visiting.remove(step_id)
        visited.add(step_id)
        return False

    return any(visit(step.id) for step in steps)


__all__ = [
    "MAX_CANDIDATE_BYTES",
    "CandidateValidationError",
    "DeterministicFakeModelAdapter",
    "KnownModelFailure",
    "ModelAdapter",
    "ModelAdapterError",
    "ModelRequest",
    "ModelResponse",
    "ModelUsage",
    "PlanningCandidate",
    "PlanningStepCandidate",
    "UnknownModelOutcome",
    "ValidatedPlanningCandidate",
    "validate_planning_candidate",
]
