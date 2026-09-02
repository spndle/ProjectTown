from __future__ import annotations

import json
from typing import Any

import httpx

from backend.app.provider_secrets import resolve_provider_connection
from backend.app.v1.model_runtime import ModelCallCoordinator
from backend.app.v1.provider_summary import parse_structured_goal_summary
from backend.app.v1.qwen_adapter import QwenDashScopeAdapter
from backend.app.v1.storage import V1Storage
from tests.conftest import APIContext


def _quest(api: APIContext) -> dict[str, Any]:
    draft = api.client.post(
        "/api/v2/quests",
        json={
            "goal": "Synthetic Qwen evaluation binding",
            "template_id": "project_brief",
            "budget": {"max_tokens": 100000},
        },
    ).json()
    response = api.client.post(
        f"/api/v2/quests/{draft['id']}/confirm",
        json={
            "expected_state_version": draft["state_version"],
            "expected_contract_version": 1,
        },
    )
    assert response.status_code == 200
    return response.json()


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


def _adapter(handler: Any) -> QwenDashScopeAdapter:
    connection = resolve_provider_connection(
        "qwen",
        environ={
            "DASHSCOPE_BASE_URL": "https://workspace-1.cn-beijing.maas.aliyuncs.com/api/v1",
            "DASHSCOPE_API_KEY": "CANARY_QWEN_KEY",
            "DASHSCOPE_MODEL": "qwen-plus",
        },
    )
    return QwenDashScopeAdapter(
        connection=connection, transport=httpx.MockTransport(handler)
    )


def _success() -> dict[str, object]:
    candidate = {
        "schema_version": 1,
        "id": "candidate_qwen",
        "version": 1,
        "summary": "Synthetic.",
        "steps": [
            {
                "id": "step_qwen",
                "title": "Read",
                "description": "",
                "tool_name": "read",
                "tool_args": {},
                "dependencies": [],
            }
        ],
    }
    return {
        "status_code": 200,
        "code": "",
        "output": {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": json.dumps(candidate)},
                }
            ]
        },
        "usage": {"input_tokens": 12, "output_tokens": 5},
    }


def _run(
    api: APIContext, adapter: QwenDashScopeAdapter, quest: dict[str, Any], key: str
):
    summary = parse_structured_goal_summary(_summary())
    return ModelCallCoordinator(api.app.state.runtime_storage, adapter).run(
        quest_id=quest["id"],
        idempotency_key=key,
        prompt_version="phase2-qwen-planning-v1",
        input_payload=summary.canonical_payload(),
        allowed_tools=["read"],
        sanitized_parameters={
            "structured_goal_summary": summary.canonical_payload(),
            "structured_goal_summary_hash": summary.summary_hash,
        },
        reserved_tokens=4608,
        max_output_tokens=512,
        expected_state_version=quest["state_version"],
        adapter_label="qwen-dashscope-native",
        model_label="qwen-plus",
        cost_reservation=V1Storage.phase1c_cost_reservation(
            4096, 512, provider="qwen", model="qwen-plus"
        ),
        retain_candidate=False,
    )


def test_qwen_success_replay_and_actual_cost_are_hash_only(api: APIContext) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_success())

    quest, adapter = _quest(api), _adapter(handler)
    first, replay = (
        _run(api, adapter, quest, "qwen-success"),
        _run(api, adapter, quest, "qwen-success"),
    )
    assert (
        first.outcome == "validated_current" and replay.idempotent_replay and calls == 1
    )
    record = api.app.state.runtime_storage.get_model_call(first.call_id or "")
    assert record and record["attempts"][0]["candidate"] is None
    assert record["attempts"][0]["cost"]["cost_microunits"] == 22
    assert (
        api.app.state.runtime_storage.model_cost_usage(
            provider="qwen", model="qwen-plus"
        )["settled_micro_cny"]
        == 22
    )


def test_qwen_known_timeout_server_and_budget_rejection(api: APIContext) -> None:
    quest = _quest(api)
    limited = _run(
        api, _adapter(lambda request: httpx.Response(429)), quest, "qwen-limited"
    )
    assert limited.outcome == "failed" and limited.error_code == "QWEN_RATE_LIMIT"
    unknown = _run(
        api, _adapter(lambda request: httpx.Response(500)), quest, "qwen-server"
    )
    assert (
        unknown.outcome == "unknown_outcome"
        and unknown.error_code == "QWEN_SERVER_UNKNOWN"
    )

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("synthetic", request=request)

    timed_out = _run(api, _adapter(timeout), quest, "qwen-timeout")
    assert (
        timed_out.outcome == "unknown_outcome"
        and timed_out.error_code == "QWEN_TIMEOUT_UNKNOWN"
    )
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_success())

    summary = parse_structured_goal_summary(_summary())
    rejected = ModelCallCoordinator(
        api.app.state.runtime_storage, _adapter(handler)
    ).run(
        quest_id=quest["id"],
        idempotency_key="qwen-cost-reject",
        prompt_version="phase2-qwen-planning-v1",
        input_payload=summary.canonical_payload(),
        allowed_tools=["read"],
        sanitized_parameters={
            "structured_goal_summary": summary.canonical_payload(),
            "structured_goal_summary_hash": summary.summary_hash,
        },
        reserved_tokens=32768,
        max_output_tokens=32768,
        expected_state_version=quest["state_version"],
        adapter_label="qwen-dashscope-native",
        model_label="qwen-plus",
        cost_reservation=V1Storage.phase1c_cost_reservation(
            500001, 0, provider="qwen", model="qwen-plus"
        ),
        retain_candidate=False,
    )
    assert rejected.outcome == "budget_rejected" and calls == 0
