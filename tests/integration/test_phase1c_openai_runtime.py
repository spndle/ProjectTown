from __future__ import annotations

import json
from typing import Any

import httpx

from backend.app.provider_secrets import resolve_provider_connection
from backend.app.v1.model_runtime import ModelCallCoordinator
from backend.app.v1.openai_adapter import OPENAI_MODEL_SNAPSHOT, OpenAIResponsesAdapter
from backend.app.v1.provider_summary import parse_structured_goal_summary
from backend.app.v1.storage import V1Storage
from tests.conftest import APIContext


def _quest(api: APIContext, max_tokens: int = 20_000) -> dict[str, Any]:
    created = api.client.post(
        "/api/v2/quests",
        json={
            "goal": "Create synthetic Phase 1C evaluation binding",
            "template_id": "project_brief",
            "budget": {"max_tokens": max_tokens},
        },
    )
    assert created.status_code == 201
    draft = created.json()
    confirmed = api.client.post(
        f"/api/v2/quests/{draft['id']}/confirm",
        json={
            "expected_state_version": draft["state_version"],
            "expected_contract_version": 1,
        },
    )
    assert confirmed.status_code == 200
    return confirmed.json()


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


def _adapter(handler) -> OpenAIResponsesAdapter:  # type: ignore[no-untyped-def]
    return OpenAIResponsesAdapter(
        connection=resolve_provider_connection(
            "openai",
            environ={
                "OPENAI_BASE_URL": "https://api.openai.com/v1",
                "OPENAI_API_KEY": "CANARY_RUNTIME_KEY",
                "OPENAI_MODEL": OPENAI_MODEL_SNAPSHOT,
            },
        ),
        transport=httpx.MockTransport(handler),
    )


def _run(
    api: APIContext,
    adapter: OpenAIResponsesAdapter,
    quest: dict[str, Any],
    key: str = "openai-phase1c",
):
    summary = parse_structured_goal_summary(_summary())
    return ModelCallCoordinator(api.app.state.runtime_storage, adapter).run(
        quest_id=quest["id"],
        idempotency_key=key,
        prompt_version="phase1c-openai-planning-v1",
        input_payload=summary.canonical_payload(),
        allowed_tools=list(summary.allowed_tools),
        sanitized_parameters={
            "structured_goal_summary": summary.canonical_payload(),
            "structured_goal_summary_hash": summary.summary_hash,
        },
        reserved_tokens=4_608,
        max_output_tokens=512,
        expected_state_version=quest["state_version"],
        adapter_label="openai-responses",
        model_label=OPENAI_MODEL_SNAPSHOT,
        cost_reservation=V1Storage.phase1c_cost_reservation(4096, 512),
        retain_candidate=False,
    )


def test_mock_success_is_hash_only_and_idempotent(api: APIContext) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        candidate = {
            "schema_version": 1,
            "id": "candidate_mock",
            "version": 1,
            "summary": "Synthetic.",
            "steps": [
                {
                    "id": "step_mock",
                    "title": "Read",
                    "description": "",
                    "tool_name": "read",
                    "tool_args": {},
                    "dependencies": [],
                }
            ],
        }
        return httpx.Response(
            200,
            json={
                "model": OPENAI_MODEL_SNAPSHOT,
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": json.dumps(candidate)}
                        ],
                    }
                ],
                "usage": {"input_tokens": 12, "output_tokens": 5, "total_tokens": 17},
            },
        )

    quest = _quest(api)
    adapter = _adapter(handler)
    first = _run(api, adapter, quest)
    replay = _run(api, adapter, quest)
    assert first.outcome == "validated_current"
    assert replay.idempotent_replay is True
    assert calls == 1
    record = api.app.state.runtime_storage.get_model_call(first.call_id or "")
    assert record and record["attempts"][0]["candidate"] is None
    assert api.app.state.runtime_storage.model_cost_usage()["settled_micro_cny"] == 104


def test_rate_limit_settles_zero_and_timeout_holds_reservation(api: APIContext) -> None:
    quest = _quest(api)
    limited = _run(
        api, _adapter(lambda request: httpx.Response(429)), quest, "rate-limit"
    )
    assert limited.outcome == "failed"
    assert limited.error_code == "OPENAI_RATE_LIMIT"

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("synthetic", request=request)

    unknown = _run(api, _adapter(timeout), quest, "timeout")
    assert unknown.outcome == "unknown_outcome"
    assert unknown.error_code == "OPENAI_TIMEOUT_UNKNOWN"
    usage = api.app.state.runtime_storage.model_cost_usage()
    assert usage["settled_micro_cny"] == 0
    assert (
        usage["held_micro_cny"]
        == V1Storage.phase1c_cost_reservation(4096, 512)["reserved_micro_cny"]
    )


def test_cost_budget_rejection_never_dispatches_transport(api: APIContext) -> None:
    called = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called += 1
        return httpx.Response(500)

    quest = _quest(api, max_tokens=100_000)
    summary = parse_structured_goal_summary(_summary())
    result = ModelCallCoordinator(api.app.state.runtime_storage, _adapter(handler)).run(
        quest_id=quest["id"],
        idempotency_key="cost-reject",
        prompt_version="phase1c-openai-planning-v1",
        input_payload=summary.canonical_payload(),
        allowed_tools=["read"],
        sanitized_parameters={
            "structured_goal_summary": summary.canonical_payload(),
            "structured_goal_summary_hash": summary.summary_hash,
        },
        reserved_tokens=32_768,
        max_output_tokens=32_000,
        expected_state_version=quest["state_version"],
        adapter_label="openai-responses",
        model_label=OPENAI_MODEL_SNAPSHOT,
        cost_reservation=V1Storage.phase1c_cost_reservation(4096, 32_000),
        retain_candidate=False,
    )
    assert result.outcome == "budget_rejected"
    assert called == 0
