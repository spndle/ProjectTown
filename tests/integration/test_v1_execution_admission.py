from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from backend.app.agent import RuleBasedAgent
from backend.app.errors import AppError
from backend.app.tools import Sandbox, build_default_registry
from backend.app.v1.gateway import ToolGateway
from backend.app.v1.models import Budget, QuestConfirm, QuestCreate
from backend.app.v1.service import V1QuestService
from backend.app.v1.storage import V1Storage


def _make_service(
    tmp_path: Path, *, lease_seconds: float = 0.06
) -> tuple[V1QuestService, V1Storage, Sandbox]:
    storage = V1Storage(tmp_path / "projecttown.db")
    sandbox = Sandbox(tmp_path / "sandbox")
    tools = build_default_registry(sandbox)
    service = V1QuestService(
        storage=storage,
        agent=RuleBasedAgent(),
        sandbox=sandbox,
        tools=tools,
        gateway=ToolGateway(tools, storage),
        max_workers=1,
        lease_seconds=lease_seconds,
    )
    return service, storage, sandbox


def _start(
    service: V1QuestService, *, workspace: str, budget: Budget | None = None
) -> str:
    draft = service.create_quest(
        QuestCreate(
            goal="Create a project brief",
            template_id="project_brief",
            workspace=workspace,
            budget=budget or Budget(),
        )
    )
    service.confirm_quest(
        draft["id"],
        QuestConfirm(
            expected_state_version=draft["state_version"], expected_contract_version=1
        ),
    )
    service.start_quest(draft["id"])
    return draft["id"]


def _wait(predicate, *, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition was not reached")


def test_queued_time_does_not_start_budget_before_admission(tmp_path: Path) -> None:
    service, storage, _sandbox = _make_service(tmp_path, lease_seconds=0.2)
    occupied = threading.Event()
    release = threading.Event()
    try:
        service._executor.submit(lambda: (occupied.set(), release.wait(2)))
        assert occupied.wait(1)
        quest_id = _start(
            service,
            workspace="queued",
            budget=Budget(max_seconds=0.08),
        )
        time.sleep(0.12)
        scheduled = service.get_quest(quest_id)
        assert scheduled["status"] == "running"
        assert scheduled["started_at"] is None
        release.set()
        _wait(lambda: service.get_quest(quest_id)["started_at"] is not None)
        state = service.get_quest(quest_id)
        assert state["status"] != "budget_exhausted"
    finally:
        release.set()
        service.close()
        storage.close()


def test_worker_heartbeat_keeps_actual_execution_lease_alive(tmp_path: Path) -> None:
    service, storage, _sandbox = _make_service(tmp_path, lease_seconds=0.06)
    entered = threading.Event()
    release = threading.Event()
    original = service.gateway.execute

    def blocking_execute(**kwargs):
        entered.set()
        assert release.wait(2)
        return original(**kwargs)

    service.gateway.execute = blocking_execute  # type: ignore[method-assign]
    try:
        quest_id = _start(service, workspace="heartbeat", budget=Budget(max_seconds=3))
        assert entered.wait(1)
        time.sleep(0.15)
        assert not storage.acquire_lease(quest_id, "other-owner", 0.06)
        release.set()
        _wait(
            lambda: (
                service.get_quest(quest_id)["status"]
                in {"completed", "waiting_user", "failed"}
            )
        )
        service.close(wait=True)
        assert not any(
            thread.name.startswith("projecttown-lease-heartbeat-")
            for thread in threading.enumerate()
        )
    finally:
        release.set()
        service.close()
        storage.close()


def test_closed_queued_worker_pauses_without_admission(tmp_path: Path) -> None:
    service, storage, _sandbox = _make_service(tmp_path)
    occupied = threading.Event()
    release = threading.Event()
    try:
        service._executor.submit(lambda: (occupied.set(), release.wait(2)))
        assert occupied.wait(1)
        quest_id = _start(service, workspace="shutdown")
        service._closed = True
        release.set()
        _wait(lambda: service.get_quest(quest_id)["status"] == "paused")
        state = service.get_quest(quest_id)
        assert state["error"]["code"] == "SERVICE_SHUTDOWN"
        assert "ExecutionAdmitted" not in {
            event["event_type"] for event in service.get_events(quest_id)
        }
    finally:
        release.set()
        service.close()
        storage.close()


def test_lost_lease_after_gateway_returns_does_not_verify_or_complete(
    tmp_path: Path,
) -> None:
    service, storage, _sandbox = _make_service(tmp_path, lease_seconds=0.06)
    entered = threading.Event()
    release = threading.Event()
    original_execute = service.gateway.execute

    def blocking_execute(**kwargs):
        entered.set()
        assert release.wait(2)
        return original_execute(**kwargs)

    renew_failed = threading.Event()

    def lose_renewal(*_args, **_kwargs):
        renew_failed.set()
        return False

    service.gateway.execute = blocking_execute  # type: ignore[method-assign]
    service.storage.renew_lease = lose_renewal  # type: ignore[method-assign]
    try:
        quest_id = _start(service, workspace="lost-lease", budget=Budget(max_seconds=3))
        assert entered.wait(1)
        assert renew_failed.wait(1)

        def replacement_claimed() -> bool:
            return storage.acquire_lease(quest_id, "replacement-owner", 1)

        _wait(replacement_claimed)
        release.set()
        _wait(
            lambda: (
                not any(
                    thread.name.startswith("projecttown-lease-heartbeat-")
                    for thread in threading.enumerate()
                )
            )
        )
        event_types = [event["event_type"] for event in service.get_events(quest_id)]
        assert "MilestoneVerificationStarted" not in event_types
        assert "QuestCompleted" not in event_types
        assert "QuestFailed" not in event_types
        owner = storage._conn.execute(
            "SELECT owner FROM v1_leases WHERE quest_id=?", (quest_id,)
        ).fetchone()["owner"]
        assert owner == "replacement-owner"
    finally:
        release.set()
        service.close()
        storage.close()


def test_baseline_persistence_failure_stops_before_first_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, storage, _sandbox = _make_service(tmp_path, lease_seconds=0.2)
    calls: list[dict] = []

    def lost_before_baseline(*_args, **_kwargs):
        raise ValueError("live execution lease is not held by owner")

    def unexpected_tool(**kwargs):
        calls.append(kwargs)
        raise AssertionError("tool execution must not start after baseline lease loss")

    monkeypatch.setattr(storage, "save_baseline_snapshot", lost_before_baseline)
    monkeypatch.setattr(service.gateway, "execute", unexpected_tool)
    try:
        quest_id = _start(
            service, workspace="baseline-lease-loss", budget=Budget(max_seconds=3)
        )
        _wait(
            lambda: (
                not any(
                    thread.name.startswith("projecttown-lease-heartbeat-")
                    for thread in threading.enumerate()
                )
            )
        )
        assert calls == []
        assert storage.list_tool_actions(quest_id) == []
        assert storage.get_baseline_snapshot(quest_id) is None
    finally:
        service.close()
        storage.close()


def test_baseline_structure_error_reaches_runtime_failure_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, storage, _sandbox = _make_service(tmp_path, lease_seconds=0.2)

    def malformed_baseline(*_args, **_kwargs):
        raise ValueError("baseline snapshot execution admission is invalid")

    monkeypatch.setattr(storage, "save_baseline_snapshot", malformed_baseline)
    try:
        quest_id = _start(
            service, workspace="baseline-structure-error", budget=Budget(max_seconds=3)
        )
        _wait(lambda: service.get_quest(quest_id)["status"] == "failed")
        state = service.get_quest(quest_id)
        assert state["error"]["code"] == "RUNTIME_ERROR"
        assert storage.list_tool_actions(quest_id) == []
    finally:
        service.close()
        storage.close()


def test_final_verifier_lease_loss_persists_no_final_results(tmp_path: Path) -> None:
    service, storage, _sandbox = _make_service(tmp_path, lease_seconds=0.06)
    verifier_entered = threading.Event()
    release_verifier = threading.Event()
    renewal_failed = threading.Event()
    original_verify_all = service.verifier.verify_all
    original_renew = storage.renew_lease

    def blocking_verify_all(*args, **kwargs):
        verifier_entered.set()
        assert release_verifier.wait(2)
        return original_verify_all(*args, **kwargs)

    def renew_until_final(*args, **kwargs):
        if verifier_entered.is_set():
            renewal_failed.set()
            return False
        return original_renew(*args, **kwargs)

    service.verifier.verify_all = blocking_verify_all  # type: ignore[method-assign]
    storage.renew_lease = renew_until_final  # type: ignore[method-assign]
    try:
        quest_id = _start(
            service, workspace="final-lease-loss", budget=Budget(max_seconds=3)
        )
        assert verifier_entered.wait(2)
        evidence_before = len(service.get_evidence(quest_id))
        results_before = len(storage.list_verification_results(quest_id))
        assert renewal_failed.wait(1)
        release_verifier.set()
        _wait(
            lambda: (
                not any(
                    thread.name.startswith("projecttown-lease-heartbeat-")
                    for thread in threading.enumerate()
                )
            )
        )
        assert len(service.get_evidence(quest_id)) == evidence_before
        assert len(storage.list_verification_results(quest_id)) == results_before
        event_types = {event["event_type"] for event in service.get_events(quest_id)}
        assert "QuestVerificationStarted" in event_types
        assert "QuestCompleted" not in event_types
        assert "ArtifactReviewRequested" not in event_types
        assert "QuestFailed" not in event_types
        assert [
            item
            for item in storage.list_workspace_snapshots(quest_id)
            if item["kind"] == "final"
        ] == []
        assert storage.list_artifact_provenance(quest_id) == []
    finally:
        release_verifier.set()
        service.close()
        storage.close()


def test_submit_failure_is_evented_as_scheduling_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, storage, _sandbox = _make_service(tmp_path)
    try:
        draft = service.create_quest(
            QuestCreate(
                goal="Create a project brief",
                template_id="project_brief",
                workspace="submit-failure",
            )
        )
        planned = service.confirm_quest(
            draft["id"],
            QuestConfirm(
                expected_state_version=draft["state_version"],
                expected_contract_version=1,
            ),
        )
        monkeypatch.setattr(
            service._executor,
            "submit",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("no worker")),
        )
        with pytest.raises(AppError) as error:
            service.start_quest(
                draft["id"], expected_state_version=planned["state_version"]
            )
        assert error.value.code == "SCHEDULING_FAILED"
        state = service.get_quest(draft["id"])
        assert state["status"] == "failed"
        assert state["error"]["code"] == "SCHEDULING_FAILED"
        assert "QuestSchedulingFailed" in {
            event["event_type"] for event in service.get_events(draft["id"])
        }
    finally:
        service.close()
        storage.close()


def test_lost_lease_during_replan_does_not_persist_replan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, storage, _sandbox = _make_service(tmp_path)
    lost = threading.Event()
    try:
        draft = service.create_quest(
            QuestCreate(
                goal="Create a project brief",
                template_id="project_brief",
                workspace="lost-replan",
            )
        )
        planned = service.confirm_quest(
            draft["id"],
            QuestConfirm(
                expected_state_version=draft["state_version"],
                expected_contract_version=1,
            ),
        )
        state = service.get_quest(draft["id"])
        milestone = state["milestones"][0]
        plans_before = storage._conn.execute(
            "SELECT COUNT(*) AS count FROM v1_plan_versions WHERE quest_id=?",
            (draft["id"],),
        ).fetchone()["count"]

        def lose_during_replan(*_args, **_kwargs):
            lost.set()
            return {"requires_user": False, "plan": {"milestones": []}}

        monkeypatch.setattr("backend.app.v1.service.replan_plan", lose_during_replan)
        assert not service._handle_replan(
            state, milestone, ["verification failed"], lost
        )
        event_types = {event["event_type"] for event in service.get_events(draft["id"])}
        assert "ReplanningStarted" not in event_types
        assert "ReplanNeedsUser" not in event_types
        plans_after = storage._conn.execute(
            "SELECT COUNT(*) AS count FROM v1_plan_versions WHERE quest_id=?",
            (draft["id"],),
        ).fetchone()["count"]
        assert plans_after == plans_before
        assert planned["status"] == "planned"
    finally:
        service.close()
        storage.close()
