"""Strict, outbound-safe summaries for the isolated real-model evaluation.

This module intentionally has no free-text fields.  It is the only user-task
description a Phase 1C provider adapter is allowed to serialize.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..runtime import stable_hash

_TOOL_PATTERN = "^[A-Za-z][A-Za-z0-9_.-]{0,63}$"
_FORBIDDEN_KEYS = frozenset(
    {
        "artifact",
        "artifact_id",
        "authorization",
        "credential",
        "evidence",
        "event",
        "file",
        "goal",
        "goal_text",
        "id",
        "internal_id",
        "log",
        "password",
        "path",
        "prompt",
        "rag",
        "raw_goal",
        "response",
        "secret",
        "token",
        "workspace",
    }
)
_CANARY_MARKERS = ("CANARY", "BEGIN PRIVATE", "RAW_GOAL")

# This fixed allowlist deliberately describes only the synthetic evaluation,
# not the application's full gateway surface.
EVALUATION_TOOL_ALLOWLIST = frozenset(
    {"check_markdown", "inspect", "read", "write_file"}
)


class SummaryValidationError(ValueError):
    """Safe rejection that never includes untrusted input."""

    def __init__(self, code: str = "INVALID_STRUCTURED_GOAL_SUMMARY") -> None:
        self.code = code
        super().__init__("structured goal summary rejected")


class StructuredGoalSummary(BaseModel):
    """A bounded enum-and-integer-only technical evaluation input."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal[1] = 1
    task_category: Literal["analysis", "documentation", "planning"]
    deliverable_kind: Literal["implementation_plan", "project_brief", "validation_plan"]
    complexity: Literal["low", "medium", "high"]
    acceptance_checks: list[
        Literal[
            "constraints_listed",
            "risks_listed",
            "scope_defined",
            "success_criteria_listed",
        ]
    ] = Field(min_length=1, max_length=4)
    allowed_tools: list[str] = Field(min_length=1, max_length=4)
    max_steps: int = Field(ge=1, le=8)

    @field_validator("acceptance_checks")
    @classmethod
    def checks_are_unique(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("acceptance checks must be unique")
        return value

    @field_validator("allowed_tools")
    @classmethod
    def tools_are_allowlisted(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value) or any(
            not re.fullmatch(_TOOL_PATTERN, item)
            or item not in EVALUATION_TOOL_ALLOWLIST
            for item in value
        ):
            raise ValueError("allowed tools must be unique and allowlisted")
        return value

    def canonical_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @property
    def summary_hash(self) -> str:
        return stable_hash(self.canonical_payload())


def parse_structured_goal_summary(value: object) -> StructuredGoalSummary:
    """Parse only the narrow safe schema and collapse all details on failure."""

    if not isinstance(value, Mapping):
        raise SummaryValidationError()
    _reject_forbidden_content(value)
    try:
        return StructuredGoalSummary.model_validate(value)
    except ValidationError:
        raise SummaryValidationError() from None


def _reject_forbidden_content(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or key.lower() in _FORBIDDEN_KEYS:
                raise SummaryValidationError("PROHIBITED_SUMMARY_CONTENT")
            _reject_forbidden_content(child)
    elif isinstance(value, list):
        for child in value:
            _reject_forbidden_content(child)
    elif isinstance(value, str) and any(
        marker in value.upper() for marker in _CANARY_MARKERS
    ):
        raise SummaryValidationError("PROHIBITED_SUMMARY_CONTENT")


__all__ = [
    "EVALUATION_TOOL_ALLOWLIST",
    "StructuredGoalSummary",
    "SummaryValidationError",
    "parse_structured_goal_summary",
]
