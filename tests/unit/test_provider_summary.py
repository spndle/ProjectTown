from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.app.v1.provider_summary import (
    StructuredGoalSummary,
    SummaryValidationError,
    parse_structured_goal_summary,
)


def _payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "task_category": "planning",
        "deliverable_kind": "implementation_plan",
        "complexity": "low",
        "acceptance_checks": ["scope_defined", "constraints_listed"],
        "allowed_tools": ["read", "check_markdown"],
        "max_steps": 3,
    }


def test_summary_is_strict_deterministic_and_bounded() -> None:
    first = parse_structured_goal_summary(_payload())
    second = parse_structured_goal_summary(_payload())
    assert isinstance(first, StructuredGoalSummary)
    assert first.summary_hash == second.summary_hash
    assert first.canonical_payload() == second.canonical_payload()
    with pytest.raises(ValidationError):
        StructuredGoalSummary.model_validate({**_payload(), "extra": "no"})
    with pytest.raises(SummaryValidationError) as raised:
        parse_structured_goal_summary({**_payload(), "max_steps": 9})
    assert str(raised.value) == "structured goal summary rejected"


@pytest.mark.parametrize(
    "mutated",
    [
        {"goal": "CANARY_RAW_GOAL"},
        {"workspace": "CANARY_WORKSPACE"},
        {"nested": {"prompt": "CANARY_PROMPT"}},
        {"nested": ["CANARY_UNIT_SECRET"]},
        {"allowed_tools": ["arbitrary_tool"]},
        {"acceptance_checks": ["scope_defined", "scope_defined"]},
    ],
)
def test_summary_rejects_raw_or_unallowlisted_content(
    mutated: dict[str, object],
) -> None:
    payload = _payload()
    payload.update(mutated)
    with pytest.raises(SummaryValidationError) as raised:
        parse_structured_goal_summary(payload)
    assert raised.value.code in {
        "INVALID_STRUCTURED_GOAL_SUMMARY",
        "PROHIBITED_SUMMARY_CONTENT",
    }
    assert "CANARY" not in str(raised.value)
