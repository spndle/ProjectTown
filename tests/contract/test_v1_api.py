from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.tools import Sandbox, build_default_registry


def _wait_for_status(
    client: TestClient,
    quest_id: str,
    expected: set[str],
    timeout: float = 5,
) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/v2/quests/{quest_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] in expected:
            return last
        time.sleep(0.01)
    pytest.fail(f"Quest did not reach {expected}: {last}")


def _draft_confirm_run(client: TestClient, workspace: str) -> str:
    draft_response = client.post(
        "/api/v2/quests",
        json={
            "goal": "Create a verified project brief",
            "template_id": "project_brief",
            "workspace": workspace,
        },
    )
    assert draft_response.status_code == 201, draft_response.text
    draft = draft_response.json()
    assert draft["status"] == "draft"
    confirm_response = client.post(
        f"/api/v2/quests/{draft['id']}/confirm",
        json={
            "expected_state_version": draft["state_version"],
            "expected_contract_version": 1,
            "approved": True,
        },
    )
    assert confirm_response.status_code == 200, confirm_response.text
    run_response = client.post(
        f"/api/v2/quests/{draft['id']}/run",
        json={"expected_state_version": confirm_response.json()["state_version"]},
    )
    assert run_response.status_code == 202, run_response.text
    return draft["id"]


def test_v2_quest_history_search_filters_and_pagination(tmp_path: Path) -> None:
    app = create_app(
        {
            "database_path": tmp_path / "projecttown.db",
            "sandbox_root": tmp_path / "sandbox",
            "runtime_max_workers": 1,
        }
    )
    with TestClient(app) as client:
        first = client.post(
            "/api/v2/quests", json={"goal": "Build a Unicode STRASSE guide"}
        ).json()
        second = client.post(
            "/api/v2/quests", json={"goal": "Build a second guide"}
        ).json()
        unfiltered = client.get("/api/v2/quests")
        assert unfiltered.status_code == 200
        unfiltered_payload = unfiltered.json()
        assert set(unfiltered_payload) == {"items", "total"}
        assert unfiltered_payload["total"] == 2
        assert {item["id"] for item in unfiltered_payload["items"]} == {
            first["id"],
            second["id"],
        }
        assert set(unfiltered_payload["items"][0]) == set(first)
        assert set(unfiltered_payload["items"][1]) == set(second)

        matched = client.get("/api/v2/quests?q=stra%C3%9Fe&status=draft")
        assert matched.status_code == 200
        assert matched.json()["items"] == [first]
        assert matched.json()["total"] == 1
        paged = client.get("/api/v2/quests?status=draft&offset=1&limit=1")
        assert paged.status_code == 200
        assert paged.json()["items"] == [unfiltered_payload["items"][1]]
        assert paged.json()["total"] == 2
        repeated = client.get("/api/v2/quests?status=draft&status=planned")
        assert repeated.status_code == 200
        assert repeated.json()["total"] == 2
        assert client.get("/api/v2/quests?q=missing").json() == {
            "items": [],
            "total": 0,
        }
        assert client.get("/api/v2/quests?offset=-1").status_code == 422
        assert client.get("/api/v2/quests?limit=0").status_code == 422
        assert client.get("/api/v2/quests?limit=101").status_code == 422
        assert client.get("/api/v2/quests?status=unknown").status_code == 422


def test_v2_rest_websocket_evidence_and_benchmark(tmp_path: Path):
    app = create_app(
        {
            "database_path": tmp_path / "projecttown.db",
            "sandbox_root": tmp_path / "sandbox",
            "runtime_max_workers": 1,
        }
    )
    with TestClient(app) as client:
        runtime_health = client.get("/api/v2/health")
        assert runtime_health.status_code == 200
        assert runtime_health.json()["deployment"] == "single-node-sqlite"

        quest_id = _draft_confirm_run(client, "quests/api-v2")
        completed = _wait_for_status(client, quest_id, {"completed", "failed"})
        assert completed["status"] == "completed", completed.get("error")

        events = client.get(f"/api/v2/quests/{quest_id}/events").json()["items"]
        assert [item["sequence"] for item in events] == list(range(1, len(events) + 1))
        assert events[-1]["event_type"] == "QuestCompleted"
        evidence = client.get(f"/api/v2/quests/{quest_id}/evidence").json()["items"]
        assert evidence
        assert all("artifact_hash" in item for item in evidence)

        with client.websocket_connect(
            f"/ws/quests/{quest_id}?resume_after=0"
        ) as websocket:
            snapshot = websocket.receive_json()
            assert snapshot["type"] == "snapshot"
            assert snapshot["delivery"] == "ordered-at-least-once"
            first_event = websocket.receive_json()
            assert first_event["type"] == "event"
            assert first_event["event"]["sequence"] == 1

        benchmark_response = client.post(
            "/api/v2/benchmark/runs", json={"profile": "smoke", "seed": 1729}
        )
        assert benchmark_response.status_code == 201, benchmark_response.text
        benchmark = benchmark_response.json()
        assert benchmark["status"] == "completed"
        assert benchmark["runtime_simulation"] is True
        assert benchmark["row_count"] > 0
        for artifact in benchmark["artifacts"].values():
            assert Path(artifact).is_file()
        loaded = client.get(f"/api/v2/benchmark/runs/{benchmark['run_id']}")
        assert loaded.status_code == 200
        assert loaded.json()["configuration_results"]


def test_pause_then_resume_is_cooperative_and_replay_safe(tmp_path: Path):
    sandbox = Sandbox(tmp_path / "sandbox")
    tools = build_default_registry(sandbox)
    entered = __import__("threading").Event()
    release = __import__("threading").Event()
    original = tools._tools["list_directory"]

    def blocking_list(workspace, arguments):
        entered.set()
        assert release.wait(timeout=5)
        return original(workspace, arguments)

    tools._tools["list_directory"] = blocking_list
    app = create_app(
        {
            "database_path": tmp_path / "projecttown.db",
            "sandbox_root": sandbox.root,
            "runtime_max_workers": 1,
        },
        tool_registry=tools,
    )
    with TestClient(app) as client:
        quest_id = _draft_confirm_run(client, "quests/pause-resume")
        assert entered.wait(timeout=2)
        running = client.get(f"/api/v2/quests/{quest_id}").json()
        pause = client.post(
            f"/api/v2/quests/{quest_id}/pause",
            json={"expected_state_version": running["state_version"]},
        )
        assert pause.status_code == 202
        assert pause.json()["pause_requested"] is True
        release.set()
        paused = _wait_for_status(client, quest_id, {"paused"})
        assert paused["status"] == "paused"
        resume = client.post(
            f"/api/v2/quests/{quest_id}/resume",
            json={"expected_state_version": paused["state_version"]},
        )
        assert resume.status_code == 202, resume.text
        completed = _wait_for_status(client, quest_id, {"completed", "failed"})
        assert completed["status"] == "completed", completed.get("error")
        event_types = {
            item["event_type"]
            for item in client.get(f"/api/v2/quests/{quest_id}/events").json()["items"]
        }
        assert {"PauseRequested", "QuestPaused", "RecoveryCompleted"} <= event_types


def test_state_version_conflict_and_duplicate_start_are_rejected(tmp_path: Path):
    app = create_app(
        {
            "database_path": tmp_path / "projecttown.db",
            "sandbox_root": tmp_path / "sandbox",
            "runtime_max_workers": 1,
        }
    )
    with TestClient(app) as client:
        templates = client.get("/api/v2/templates")
        assert templates.status_code == 200
        assert templates.json()["total"] == 3

        draft = client.post(
            "/api/v2/quests",
            json={"goal": "Create a README", "template_id": "readme_builder"},
        ).json()
        conflict = client.post(
            f"/api/v2/quests/{draft['id']}/confirm",
            json={
                "expected_state_version": draft["state_version"] + 1,
                "expected_contract_version": 1,
            },
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "STATE_VERSION_CONFLICT"

        planned = client.post(
            f"/api/v2/quests/{draft['id']}/confirm",
            json={
                "expected_state_version": draft["state_version"],
                "expected_contract_version": 1,
            },
        )
        assert planned.status_code == 200
        missing_version = client.post(f"/api/v2/quests/{draft['id']}/run", json={})
        assert missing_version.status_code == 422
        assert missing_version.json()["error"]["code"] == "VALIDATION_ERROR"
        stale_run = client.post(
            f"/api/v2/quests/{draft['id']}/run",
            json={"expected_state_version": draft["state_version"]},
        )
        assert stale_run.status_code == 409
        assert stale_run.json()["error"]["code"] == "STATE_VERSION_CONFLICT"
        assert (
            client.post(
                f"/api/v2/quests/{draft['id']}/run",
                json={"expected_state_version": planned.json()["state_version"]},
            ).status_code
            == 202
        )
        current = client.get(f"/api/v2/quests/{draft['id']}").json()
        duplicate = client.post(
            f"/api/v2/quests/{draft['id']}/run",
            json={"expected_state_version": current["state_version"]},
        )
        assert duplicate.status_code == 409


def test_rejected_goal_contract_can_be_revised_and_confirmed(tmp_path: Path) -> None:
    app = create_app(
        {
            "database_path": tmp_path / "projecttown.db",
            "sandbox_root": tmp_path / "sandbox",
            "runtime_max_workers": 1,
        }
    )
    with TestClient(app) as client:
        draft = client.post(
            "/api/v2/quests",
            json={
                "goal": "Create a reviewable README",
                "template_id": "readme_builder",
            },
        ).json()
        rejected = client.post(
            f"/api/v2/quests/{draft['id']}/confirm",
            json={
                "expected_state_version": draft["state_version"],
                "expected_contract_version": 1,
                "approved": False,
                "note": "Clarify the goal",
            },
        )
        assert rejected.status_code == 200
        assert rejected.json()["status"] == "draft"
        assert rejected.json()["error"]["code"] == "GOAL_CONTRACT_REJECTED"

        confirmed = client.post(
            f"/api/v2/quests/{draft['id']}/confirm",
            json={
                "expected_state_version": rejected.json()["state_version"],
                "expected_contract_version": 1,
                "approved": True,
                "goal": "Create a revised and reviewable README",
            },
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["status"] == "planned"
        assert confirmed.json()["error"] is None
        assert confirmed.json()["goal"] == "Create a revised and reviewable README"
        assert (
            confirmed.json()["contract"]["goal"]
            == "Create a revised and reviewable README"
        )


def test_high_risk_action_requires_exact_decision_before_resume(tmp_path: Path) -> None:
    app = create_app(
        {
            "database_path": tmp_path / "projecttown.db",
            "sandbox_root": tmp_path / "sandbox",
            "runtime_max_workers": 1,
            "high_risk_tools": ("write_file",),
        }
    )
    with TestClient(app) as client:
        quest_id = _draft_confirm_run(client, "quests/high-risk")
        waiting = _wait_for_status(client, quest_id, {"waiting_user", "failed"})
        assert waiting["status"] == "waiting_user", waiting.get("error")
        pending = waiting["pending_approval"]
        assert pending["tool_name"] == "write_file"

        direct_resume = client.post(
            f"/api/v2/quests/{quest_id}/resume",
            json={"expected_state_version": waiting["state_version"]},
        )
        assert direct_resume.status_code == 409
        assert direct_resume.json()["error"]["code"] == "QUEST_NOT_RESUMABLE"

        broad_approval = client.post(
            f"/api/v2/quests/{quest_id}/decisions",
            json={
                "kind": "approve",
                "expected_state_version": waiting["state_version"],
                "note": "Approval without an exact action must fail",
            },
        )
        assert broad_approval.status_code == 422
        assert broad_approval.json()["error"]["code"] == "APPROVAL_TARGET_MISMATCH"

        approved = client.post(
            f"/api/v2/quests/{quest_id}/decisions",
            json={
                "kind": "approve",
                "expected_state_version": waiting["state_version"],
                "note": "Approve this exact sandbox write",
                "contract_patch": {"action_id": pending["action_id"]},
            },
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "paused"
        assert approved.json()["pending_approval"] is None

        resumed = client.post(
            f"/api/v2/quests/{quest_id}/resume",
            json={"expected_state_version": approved.json()["state_version"]},
        )
        assert resumed.status_code == 202, resumed.text
        completed = _wait_for_status(client, quest_id, {"completed", "failed"})
        assert completed["status"] == "completed", completed.get("error")
        decisions = client.get(f"/api/v2/quests/{quest_id}/decisions").json()
        assert decisions["total"] == 1
        assert decisions["items"][0]["contract_patch"] == {
            "action_id": pending["action_id"]
        }
