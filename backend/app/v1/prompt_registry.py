"""Fixed, versioned prompt material for the isolated Phase 1C evaluation."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..runtime import stable_hash

PROMPT_VERSION = "phase1c-openai-planning-v1"
PROMPT_NAME = "projecttown_phase1c_planning_candidate"

# It is deliberately static.  Callers must never persist or log the expanded
# request built from this instruction plus a structured summary.
FIXED_INSTRUCTIONS = (
    "Generate one non-executing planning candidate for the supplied synthetic "
    "structured evaluation summary. Return only JSON matching the schema. "
    "Do not include secrets, identifiers, paths, or unprovided task details."
)

PLANNING_CANDIDATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "id", "version", "summary", "steps"],
    "properties": {
        "schema_version": {"type": "integer", "const": 1},
        "id": {"type": "string", "minLength": 1, "maxLength": 120},
        "version": {"type": "integer", "minimum": 1, "maximum": 1000000},
        "summary": {"type": "string", "minLength": 1, "maxLength": 2000},
        "steps": {
            "type": "array",
            "minItems": 1,
            "maxItems": 8,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "id",
                    "title",
                    "description",
                    "tool_name",
                    "tool_args",
                    "dependencies",
                ],
                "properties": {
                    "id": {"type": "string", "minLength": 1, "maxLength": 120},
                    "title": {"type": "string", "minLength": 1, "maxLength": 240},
                    "description": {"type": "string", "maxLength": 2000},
                    "tool_name": {"type": "string", "minLength": 1, "maxLength": 120},
                    "tool_args": {"type": "object", "additionalProperties": False},
                    "dependencies": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 32,
                    },
                },
            },
        },
    },
}

PROMPT_REGISTRY_HASH = stable_hash(
    {
        "name": PROMPT_NAME,
        "version": PROMPT_VERSION,
        "instructions": FIXED_INSTRUCTIONS,
        "schema": PLANNING_CANDIDATE_SCHEMA,
    }
)


def response_text_format() -> dict[str, Any]:
    """Return a fresh strict Responses Structured Outputs declaration."""

    return {
        "type": "json_schema",
        "name": PROMPT_NAME,
        "strict": True,
        "schema": deepcopy(PLANNING_CANDIDATE_SCHEMA),
    }


__all__ = [
    "FIXED_INSTRUCTIONS",
    "PLANNING_CANDIDATE_SCHEMA",
    "PROMPT_NAME",
    "PROMPT_REGISTRY_HASH",
    "PROMPT_VERSION",
    "response_text_format",
]
