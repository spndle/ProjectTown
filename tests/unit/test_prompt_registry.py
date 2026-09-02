from __future__ import annotations

from backend.app.runtime import stable_hash
from backend.app.v1.prompt_registry import (
    FIXED_INSTRUCTIONS,
    PROMPT_NAME,
    PROMPT_REGISTRY_HASH,
    PROMPT_VERSION,
    response_text_format,
)


def test_registry_is_stable_and_returns_a_strict_fresh_schema() -> None:
    first = response_text_format()
    second = response_text_format()
    assert first == second
    assert first is not second
    assert first["type"] == "json_schema"
    assert first["name"] == PROMPT_NAME
    assert first["strict"] is True
    assert first["schema"]["additionalProperties"] is False
    assert PROMPT_VERSION.startswith("phase1c-")
    assert len(PROMPT_REGISTRY_HASH) == 64
    assert PROMPT_REGISTRY_HASH == stable_hash(
        {
            "name": PROMPT_NAME,
            "version": PROMPT_VERSION,
            "instructions": FIXED_INSTRUCTIONS,
            "schema": first["schema"],
        }
    )


def test_registry_schema_copy_cannot_mutate_next_call() -> None:
    first = response_text_format()
    first["schema"]["properties"]["id"]["maxLength"] = 1
    assert response_text_format()["schema"]["properties"]["id"]["maxLength"] == 120
