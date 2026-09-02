from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from .conftest import APIContext, wait_for_terminal_quest

QUEST_CASES: tuple[dict[str, Any], ...] = (
    {
        "template_id": "project_brief",
        "goal": "Create a concise brief for a reliable personal Agent project",
        "workspace": "quests/project-brief",
        "expected_step_count": 3,
        "expected_artifacts": ("deliverables/project_brief.md",),
    },
    {
        "template_id": "python_starter",
        "goal": "Create a minimal Python command-line starter project",
        "workspace": "quests/python-starter",
        "expected_step_count": 5,
        "expected_artifacts": ("src/main.py", "README.md"),
    },
    {
        "template_id": "readme_builder",
        "goal": "Create a README for a personal learning progress tracker",
        "workspace": "quests/readme-builder",
        "expected_step_count": 3,
        "expected_artifacts": ("README.md",),
    },
)


def test_health_and_templates(api: APIContext) -> None:
    health_response = api.client.get("/health")
    assert health_response.status_code == 200
    assert health_response.json() == {
        "status": "ok",
        "version": "3.0.0",
        "agent": "rule_based",
        "database": "ok",
    }
    assert health_response.headers["X-Request-ID"]

    templates_response = api.client.get("/api/v1/templates")
    assert templates_response.status_code == 200
    payload = templates_response.json()
    assert payload["total"] == 3
    assert {item["id"] for item in payload["items"]} == {
        "project_brief",
        "python_starter",
        "readme_builder",
    }
    for item in payload["items"]:
        assert item["name"]
        assert item["description"]
        assert 3 <= item["estimated_steps"] <= 5
        assert item["expected_artifacts"]


@pytest.mark.parametrize(
    "case",
    QUEST_CASES,
    ids=[case["template_id"] for case in QUEST_CASES],
)
def test_fixed_quest_completes_with_artifacts_and_traces(
    api: APIContext, case: dict[str, Any]
) -> None:
    request = {
        "template_id": case["template_id"],
        "goal": case["goal"],
        "workspace": case["workspace"],
    }
    create_response = api.client.post("/api/v1/quests", json=request)
    assert create_response.status_code == 201, create_response.text
    created = create_response.json()
    assert created["status"] == "planned"
    assert created["template_id"] == case["template_id"]
    assert created["workspace"] == case["workspace"]
    assert created["progress"] == 0
    assert len(created["milestones"]) == case["expected_step_count"]
    assert [item["position"] for item in created["milestones"]] == list(
        range(1, case["expected_step_count"] + 1)
    )

    run_response = api.client.post(f"/api/v1/quests/{created['id']}/run")
    assert run_response.status_code == 202, run_response.text
    assert run_response.json() == {
        "quest_id": created["id"],
        "status": "running",
        "message": "Quest execution started",
    }

    completed = wait_for_terminal_quest(api.client, created["id"])
    assert completed["status"] == "completed", completed.get("error")
    assert completed["progress"] == 1
    assert completed["progress_percent"] == 100
    assert completed["error"] is None
    assert completed["started_at"]
    assert completed["finished_at"]
    assert all(item["status"] == "completed" for item in completed["milestones"])
    assert all(item["result"] is not None for item in completed["milestones"])
    assert all(item["error"] is None for item in completed["milestones"])

    workspace = api.sandbox_root / case["workspace"]
    for artifact in case["expected_artifacts"]:
        path = workspace / artifact
        assert path.is_file(), f"missing generated artifact: {path}"
        assert path.read_text(encoding="utf-8").strip()
    if case["template_id"] == "python_starter":
        ast.parse((workspace / "src/main.py").read_text(encoding="utf-8"))

    trace_response = api.client.get(f"/api/v1/quests/{created['id']}/traces")
    assert trace_response.status_code == 200
    trace_payload = trace_response.json()
    traces = trace_payload["items"]
    assert trace_payload["total"] == len(traces)
    assert [trace["sequence"] for trace in traces] == list(range(1, len(traces) + 1))
    trace_types = {trace["trace_type"] for trace in traces}
    assert {
        "quest_created",
        "quest_started",
        "milestone_started",
        "tool_started",
        "tool_completed",
        "milestone_completed",
        "quest_completed",
    } <= trace_types
    assert all(trace["quest_id"] == created["id"] for trace in traces)


def test_tool_failure_is_persisted_in_quest_milestone_and_traces(
    tmp_path: Path,
) -> None:
    from backend.app.main import create_app

    app = create_app(
        {
            "database_path": tmp_path / "failure.db",
            "sandbox_root": tmp_path / "sandbox",
            "max_file_bytes": 64,
            "max_workers": 1,
        }
    )
    with TestClient(app) as client:
        create_response = client.post(
            "/api/v1/quests",
            json={
                "template_id": "project_brief",
                "goal": "Create a project brief that is intentionally larger than the test limit",
                "workspace": "quests/expected-failure",
            },
        )
        assert create_response.status_code == 201
        quest_id = create_response.json()["id"]
        assert client.post(f"/api/v1/quests/{quest_id}/run").status_code == 202

        failed = wait_for_terminal_quest(client, quest_id)
        assert failed["status"] == "failed"
        assert failed["error"]["code"] == "FILE_TOO_LARGE"
        assert [item["status"] for item in failed["milestones"]] == [
            "completed",
            "failed",
            "pending",
        ]
        failed_milestone = failed["milestones"][1]
        assert failed_milestone["error"]["code"] == "FILE_TOO_LARGE"

        traces = client.get(f"/api/v1/quests/{quest_id}/traces").json()["items"]
        failed_traces = [trace for trace in traces if trace["level"] == "error"]
        assert [trace["trace_type"] for trace in failed_traces] == [
            "tool_failed",
            "quest_failed",
        ]
        assert all(
            trace["data"]["error"]["code"] == "FILE_TOO_LARGE"
            for trace in failed_traces
        )
