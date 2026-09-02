from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.agent import RuleBasedAgent
from backend.app.main import create_app
from backend.app.tools import Sandbox, build_default_registry
from backend.app.v1.gateway import ToolGateway
from backend.app.v1.models import QuestConfirm, QuestCreate
from backend.app.v1.service import V1QuestService
from backend.app.v1.storage import V1Storage


def _create_confirm(client: TestClient, workspace: str) -> dict:
    draft = client.post(
        "/api/v2/quests",
        json={
            "goal": "Create a recoverable project brief",
            "template_id": "project_brief",
            "workspace": workspace,
        },
    ).json()
    response = client.post(
        f"/api/v2/quests/{draft['id']}/confirm",
        json={
            "expected_state_version": draft["state_version"],
            "expected_contract_version": 1,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _wait(client: TestClient, quest_id: str, statuses: set[str]) -> dict:
    deadline = time.monotonic() + 5
    last = None
    while time.monotonic() < deadline:
        last = client.get(f"/api/v2/quests/{quest_id}").json()
        if last["status"] in statuses:
            return last
        time.sleep(0.01)
    pytest.fail(f"Quest did not reach {statuses}: {last}")


def test_response_loss_is_reconciled_before_resume(tmp_path: Path):
    config = {
        "database_path": tmp_path / "projecttown.db",
        "sandbox_root": tmp_path / "sandbox",
        "runtime_max_workers": 1,
    }
    app = create_app(config)
    with TestClient(app) as client:
        planned = _create_confirm(client, "quests/response-loss")
        app.state.runtime_service.gateway.faults.points.add(
            "after_effect_before_receipt"
        )
        assert (
            client.post(
                f"/api/v2/quests/{planned['id']}/run",
                json={"expected_state_version": planned["state_version"]},
            ).status_code
            == 202
        )
        paused = _wait(client, planned["id"], {"paused"})
        assert paused["recovery_required"] is True
        assert (
            client.post(
                f"/api/v2/quests/{planned['id']}/resume",
                json={"expected_state_version": paused["state_version"]},
            ).status_code
            == 202
        )
        completed = _wait(client, planned["id"], {"completed", "failed"})
        assert completed["status"] == "completed", completed.get("error")
        events = client.get(f"/api/v2/quests/{planned['id']}/events").json()["items"]
        types = [item["event_type"] for item in events]
        assert "ToolEffectUnknown" in types
        assert "RecoveryCompleted" in types
        assert types[-1] == "QuestCompleted"


def test_resume_with_legacy_action_records_unobserved_baseline_without_forgery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An upgraded runtime must not scan a workspace after a legacy action."""

    config = {
        "database_path": tmp_path / "projecttown.db",
        "sandbox_root": tmp_path / "sandbox",
        "runtime_max_workers": 1,
    }
    app = create_app(config)
    with TestClient(app) as client:
        service = app.state.runtime_service
        original_baseline = service._record_execution_baseline
        monkeypatch.setattr(
            service, "_record_execution_baseline", lambda *_args, **_kwargs: True
        )
        planned = _create_confirm(client, "quests/legacy-no-baseline")
        service.gateway.faults.points.add("after_effect_before_receipt")
        assert (
            client.post(
                f"/api/v2/quests/{planned['id']}/run",
                json={"expected_state_version": planned["state_version"]},
            ).status_code
            == 202
        )
        paused = _wait(client, planned["id"], {"paused"})
        storage = app.state.runtime_storage
        legacy_actions = storage.list_tool_actions(planned["id"])
        assert len(legacy_actions) == 1
        legacy_action_id = legacy_actions[0]["action_id"]
        assert storage.get_baseline_snapshot(planned["id"]) is None

        monkeypatch.setattr(service, "_record_execution_baseline", original_baseline)
        resumed = client.post(
            f"/api/v2/quests/{planned['id']}/resume",
            json={"expected_state_version": paused["state_version"]},
        )
        assert resumed.status_code == 202, resumed.text
        completed = _wait(client, planned["id"], {"completed", "failed"})
        assert completed["status"] == "completed", completed.get("error")

        baseline = storage.get_baseline_snapshot(planned["id"])
        assert baseline is not None
        assert baseline["status"] == "legacy_unobserved"
        assert baseline["root_hash"] is None
        assert storage.list_workspace_snapshot_entries(baseline["snapshot_id"]) == []
        assert (
            len(
                [
                    snapshot
                    for snapshot in storage.list_workspace_snapshots(planned["id"])
                    if snapshot["kind"] == "baseline"
                ]
            )
            == 1
        )
        committed = [
            event
            for event in storage.list_events(planned["id"])
            if event["event_type"] == "ToolCommitted"
            and event["payload"]["patch"]["last_receipt"]["action_id"]
            == legacy_action_id
        ]
        assert len(committed) == 1
        assert storage.replay(planned["id"]) == completed
        assert storage.validate_checkpoint(planned["id"])


def test_startup_scans_interrupted_quest_and_requires_resume(tmp_path: Path):
    config = {
        "database_path": tmp_path / "projecttown.db",
        "sandbox_root": tmp_path / "sandbox",
        "runtime_max_workers": 1,
    }
    first_app = create_app(config)
    with TestClient(first_app) as client:
        planned = _create_confirm(client, "quests/process-exit")
        storage = first_app.state.runtime_storage
        storage.append_event(
            planned["id"],
            "QuestStarted",
            {"status": "running", "started_at": planned["updated_at"]},
            planned["state_version"],
        )

    restarted_app = create_app(config)
    with TestClient(restarted_app) as client:
        recovery = client.get("/api/v2/health").json()["recovery"]
        assert recovery["interrupted_quests"] == 1
        paused = client.get(f"/api/v2/quests/{planned['id']}").json()
        assert paused["status"] == "paused"
        assert paused["recovery_required"] is True
        resume = client.post(
            f"/api/v2/quests/{planned['id']}/resume",
            json={"expected_state_version": paused["state_version"]},
        )
        assert resume.status_code == 202, resume.text
        completed = _wait(client, planned["id"], {"completed", "failed"})
        assert completed["status"] == "completed", completed.get("error")
        assert restarted_app.state.runtime_storage.validate_checkpoint(planned["id"])


def test_restart_after_committed_receipt_does_not_repeat_tool_effect(tmp_path: Path):
    database_path = tmp_path / "projecttown.db"
    sandbox_root = tmp_path / "sandbox"
    write_calls = 0

    def build_service(*, crash_after_write: bool):
        nonlocal write_calls
        storage = V1Storage(database_path)
        sandbox = Sandbox(sandbox_root)
        tools = build_default_registry(sandbox)
        write_file = tools._tools["write_file"]

        def counted_write(workspace, arguments):
            nonlocal write_calls
            write_calls += 1
            return write_file(workspace, arguments)

        tools._tools["write_file"] = counted_write
        gateway = ToolGateway(tools, storage)
        if crash_after_write:
            execute = gateway.execute
            crashed = False

            def stop_after_commit(**kwargs):
                nonlocal crashed
                receipt = execute(**kwargs)
                if kwargs["tool_name"] == "write_file" and not crashed:
                    crashed = True
                    raise SystemExit("simulated process exit after committed receipt")
                return receipt

            gateway.execute = stop_after_commit
        service = V1QuestService(
            storage=storage,
            agent=RuleBasedAgent(),
            sandbox=sandbox,
            tools=tools,
            gateway=gateway,
            max_workers=1,
        )
        return service, storage

    first, first_storage = build_service(crash_after_write=True)
    try:
        draft = first.create_quest(
            QuestCreate(
                goal="Create a recoverable project brief",
                template_id="project_brief",
                workspace="quests/atomic-receipt-restart",
            )
        )
        planned = first.confirm_quest(
            draft["id"],
            QuestConfirm(
                expected_state_version=draft["state_version"],
                expected_contract_version=1,
            ),
        )
        first.start_quest(draft["id"], planned["state_version"])
        deadline = time.monotonic() + 5
        interrupted = None
        while time.monotonic() < deadline:
            interrupted = first.get_quest(draft["id"])
            receipt = interrupted.get("last_receipt") or {}
            if (
                receipt.get("status") == "committed"
                and receipt.get("result", {}).get("path")
                == "deliverables/project_brief.md"
            ):
                break
            time.sleep(0.01)
        else:
            pytest.fail(f"write receipt was not committed: {interrupted}")
        assert interrupted is not None
        assert interrupted["status"] == "running"
        active_id = interrupted["current_milestone_id"]
        active = next(
            item for item in interrupted["milestones"] if item["id"] == active_id
        )
        assert active["attempt"] == 1
        usage_before_restart = dict(interrupted["budget_usage"])
        write_action_id = interrupted["last_receipt"]["action_id"]
        assert write_calls == 1
    finally:
        first.close()
        first_storage.close()

    restarted, restarted_storage = build_service(crash_after_write=False)
    try:
        paused = restarted.get_quest(draft["id"])
        assert paused["status"] == "paused"
        recovered_active = next(
            item for item in paused["milestones"] if item["id"] == active_id
        )
        assert recovered_active["attempt"] == 1
        assert paused["budget_usage"] == usage_before_restart

        restarted.resume_quest(draft["id"], paused["state_version"])
        completed = None
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            completed = restarted.get_quest(draft["id"])
            if completed["status"] in {"completed", "failed", "budget_exhausted"}:
                break
            time.sleep(0.01)
        else:
            pytest.fail(f"restarted Quest did not finish: {completed}")

        assert completed is not None
        assert completed["status"] == "completed", completed.get("error")
        assert write_calls == 1
        assert completed["budget_usage"]["steps"] == usage_before_restart["steps"] + 1
        assert completed["budget_usage"]["tool_calls"] == (
            usage_before_restart["tool_calls"] + 1
        )
        write_events = [
            event
            for event in restarted.get_events(draft["id"])
            if event["event_type"] == "ToolCommitted"
            and event["payload"]["patch"]["last_receipt"]["action_id"]
            == write_action_id
        ]
        assert len(write_events) == 1
    finally:
        restarted.close()
        restarted_storage.close()
