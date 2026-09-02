from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from backend.app.agent import RuleBasedAgent
from backend.app.errors import AppError
from backend.app.tools import Sandbox, build_default_registry
from backend.app.v1.gateway import ToolGateway
from backend.app.v1.models import (
    AcceptanceCriterion,
    Budget,
    DecisionCreate,
    QuestConfirm,
    QuestCreate,
)
from backend.app.v1.service import V1QuestService
from backend.app.v1.storage import V1Storage


@pytest.fixture
def runtime(tmp_path: Path):
    storage = V1Storage(tmp_path / "projecttown.db")
    sandbox = Sandbox(tmp_path / "sandbox")
    tools = build_default_registry(sandbox)
    gateway = ToolGateway(tools, storage)
    service = V1QuestService(
        storage=storage,
        agent=RuleBasedAgent(),
        sandbox=sandbox,
        tools=tools,
        gateway=gateway,
        max_workers=1,
    )
    try:
        yield service, storage, sandbox
    finally:
        service.close()
        storage.close()


def _confirm_and_start(service: V1QuestService, payload: QuestCreate) -> str:
    draft = service.create_quest(payload)
    assert draft["status"] == "draft"
    assert draft["state_version"] == 1
    planned = service.confirm_quest(
        draft["id"],
        QuestConfirm(
            expected_state_version=draft["state_version"],
            expected_contract_version=1,
        ),
    )
    assert planned["status"] == "planned"
    assert planned["contract"]["confirmed"] is True
    service.start_quest(draft["id"])
    return draft["id"]


def _wait_terminal(service: V1QuestService, quest_id: str) -> dict:
    deadline = time.monotonic() + 5
    last = None
    while time.monotonic() < deadline:
        last = service.get_quest(quest_id)
        if last["status"] in {
            "completed",
            "budget_exhausted",
            "failed",
            "waiting_user",
            "paused",
        }:
            return last
        time.sleep(0.01)
    pytest.fail(f"Quest did not reach a stable state: {last}")


def test_verified_quest_has_dag_evidence_events_and_checkpoint(runtime):
    service, storage, sandbox = runtime
    quest_id = _confirm_and_start(
        service,
        QuestCreate(
            goal="Create a minimal Python CLI",
            template_id="python_starter",
            workspace="quests/python-v1",
        ),
    )
    completed = _wait_terminal(service, quest_id)
    assert completed["status"] == "completed", completed.get("error")
    assert completed["progress"] == 1
    assert all(item["status"] == "completed" for item in completed["milestones"])
    assert all(item["evidence_ids"] for item in completed["milestones"])
    assert any(len(item["dependencies"]) == 1 for item in completed["milestones"])
    assert len(service.get_evidence(quest_id)) >= len(completed["milestones"])
    assert storage.replay(quest_id) == completed
    assert storage.validate_checkpoint(quest_id)
    assert (sandbox.root / "quests/python-v1/src/main.py").is_file()
    event_types = {item["event_type"] for item in service.get_events(quest_id)}
    assert {
        "QuestDrafted",
        "GoalContractConfirmed",
        "ToolCommitted",
        "MilestoneVerified",
        "QuestCompleted",
    } <= event_types


def test_tool_success_does_not_bypass_verifier_and_replan_is_bounded(tmp_path: Path):
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
    )
    try:
        quest_id = _confirm_and_start(
            service,
            QuestCreate(
                goal="Create a README whose required JSON check deliberately fails",
                template_id="readme_builder",
                workspace="quests/false-completion",
                budget=Budget(max_replans=1),
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="readme-deliberately-not-json",
                        kind="json_schema",
                        description="Deliberately unsatisfied: README.md is not JSON.",
                        path="README.md",
                        required=True,
                    )
                ],
            ),
        )
        stopped = _wait_terminal(service, quest_id)
        assert stopped["status"] == "waiting_user"
        assert stopped["error"]["code"] == "REPLAN_BUDGET_EXHAUSTED"
        assert stopped["progress"] < 1
        assert (sandbox.root / "quests/false-completion/README.md").is_file()
        assert "QuestCompleted" not in {
            item["event_type"] for item in service.get_events(quest_id)
        }
    finally:
        service.close()
        storage.close()


def test_watchdog_blocks_repeated_real_writes_without_verified_progress(tmp_path: Path):
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
        watchdog_threshold=2,
    )
    try:
        quest_id = _confirm_and_start(
            service,
            QuestCreate(
                goal="Create a README while detecting repeated unverified writes",
                template_id="readme_builder",
                workspace="quests/loop-detection",
                budget=Budget(max_replans=5),
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="readme-repeatedly-not-json",
                        kind="json_schema",
                        description="Deliberately unsatisfied: README.md is not JSON.",
                        path="README.md",
                        required=True,
                    )
                ],
            ),
        )
        stopped = _wait_terminal(service, quest_id)
        assert stopped["status"] == "waiting_user"
        assert stopped["error"]["code"] == "LOOP_DETECTED"
        assert (sandbox.root / "quests/loop-detection/README.md").is_file()
        assert "LoopDetected" in {
            item["event_type"] for item in service.get_events(quest_id)
        }
    finally:
        service.close()
        storage.close()


def test_budget_exhaustion_is_a_distinct_terminal_state(runtime):
    service, _storage, _sandbox = runtime
    quest_id = _confirm_and_start(
        service,
        QuestCreate(
            goal="Create a brief under an intentionally tiny step budget",
            template_id="project_brief",
            workspace="quests/budget",
            budget=Budget(max_steps=1),
        ),
    )
    stopped = _wait_terminal(service, quest_id)
    assert stopped["status"] == "budget_exhausted"
    assert stopped["error"]["code"] == "BUDGET_EXHAUSTED"
    assert stopped["progress"] < 1


def test_optional_criterion_failure_does_not_block_completion(runtime):
    service, _storage, _sandbox = runtime
    quest_id = _confirm_and_start(
        service,
        QuestCreate(
            goal="Create a brief while recording an optional failed check",
            template_id="project_brief",
            workspace="quests/optional-criterion",
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="optional-json-shape",
                    kind="json_schema",
                    description="Optional JSON shape probe",
                    path="deliverables/project_brief.md",
                    required=False,
                    required_keys=["not-present"],
                )
            ],
        ),
    )

    completed = _wait_terminal(service, quest_id)
    assert completed["status"] == "completed", completed.get("error")
    optional_results = [
        item
        for item in service.storage.list_verification_results(quest_id)
        if item["criterion_id"] == "optional-json-shape"
    ]
    assert optional_results
    assert all(item["passed"] is False for item in optional_results)


def test_concurrent_start_has_one_executor_and_one_conflict(runtime):
    service, storage, _sandbox = runtime
    draft = service.create_quest(
        QuestCreate(
            goal="Create a brief under a single execution lease",
            template_id="project_brief",
            workspace="quests/concurrent-start",
        )
    )
    planned = service.confirm_quest(
        draft["id"],
        QuestConfirm(
            expected_state_version=draft["state_version"],
            expected_contract_version=1,
        ),
    )
    acquire_lease = storage.acquire_lease
    acquisition_barrier = threading.Barrier(2)
    release_worker = threading.Event()
    execute_quest = service._execute_quest

    def synchronized_acquire(*args, **kwargs):
        acquisition_barrier.wait(timeout=2)
        return acquire_lease(*args, **kwargs)

    def blocked_execute(quest_id):
        assert release_worker.wait(timeout=2)
        execute_quest(quest_id)

    storage.acquire_lease = synchronized_acquire
    service._execute_quest = blocked_execute
    outcomes: list[tuple[str, int]] = []

    def start() -> None:
        try:
            service.start_quest(draft["id"], planned["state_version"])
            outcomes.append(("started", 202))
        except AppError as exc:
            outcomes.append((exc.code, exc.status_code))

    threads = [threading.Thread(target=start) for _ in range(2)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
        assert all(not thread.is_alive() for thread in threads)
        assert sorted(outcomes) == [
            ("QUEST_LEASE_CONFLICT", 409),
            ("started", 202),
        ]
        storage.acquire_lease = acquire_lease
        # Admission happens inside the worker; a queued future holds no lease.
        assert acquire_lease(draft["id"], "competing-owner", 30) is True
        storage.release_lease(draft["id"], "competing-owner")
    finally:
        storage.acquire_lease = acquire_lease
        service._execute_quest = execute_quest
        release_worker.set()

    completed = _wait_terminal(service, draft["id"])
    assert completed["status"] == "completed", completed.get("error")


def test_final_verification_time_counts_against_budget(runtime):
    service, _storage, _sandbox = runtime
    verify_all = service.verifier.verify_all

    def slow_final_verification(*args, **kwargs):
        time.sleep(1.05)
        return verify_all(*args, **kwargs)

    service.verifier.verify_all = slow_final_verification
    quest_id = _confirm_and_start(
        service,
        QuestCreate(
            goal="Create a brief with a bounded final verification",
            template_id="project_brief",
            workspace="quests/final-verification-budget",
            budget=Budget(max_seconds=1),
        ),
    )

    stopped = _wait_terminal(service, quest_id)
    assert stopped["status"] == "budget_exhausted"
    assert stopped["error"]["code"] == "BUDGET_EXHAUSTED"
    assert "QuestCompleted" not in {
        event["event_type"] for event in service.get_events(quest_id)
    }


def test_user_modification_versions_contract_and_replans(runtime):
    service, storage, _sandbox = runtime
    draft = service.create_quest(
        QuestCreate(
            goal="Create an initial README",
            template_id="readme_builder",
            workspace="quests/user-modification",
        )
    )
    planned = service.confirm_quest(
        draft["id"],
        QuestConfirm(
            expected_state_version=draft["state_version"],
            expected_contract_version=1,
        ),
    )
    storage.append_event(
        draft["id"],
        "UserDecisionRequested",
        {"status": "waiting_user"},
        planned["state_version"],
    )
    waiting = service.get_quest(draft["id"])

    modified = service.submit_decision(
        draft["id"],
        DecisionCreate(
            kind="modify",
            expected_state_version=waiting["state_version"],
            note="Use the revised goal",
            contract_patch={"goal": "Create a revised README"},
        ),
    )
    assert modified["status"] == "paused"
    assert modified["goal"] == "Create a revised README"
    assert modified["contract"]["goal"] == "Create a revised README"
    assert modified["contract"]["version"] == 3
    assert modified["plan_version"] == 2
    assert modified["pending_approval"] is None
    assert {event["event_type"] for event in service.get_events(draft["id"])} >= {
        "GoalContractModified",
        "PlanReplanned",
        "UserModificationApplied",
    }


def test_invalid_modification_of_completed_work_has_no_partial_writes(runtime):
    service, storage, _sandbox = runtime
    draft = service.create_quest(
        QuestCreate(
            goal="Create an immutable completed brief",
            template_id="project_brief",
            workspace="quests/completed-modification",
        )
    )
    planned = service.confirm_quest(
        draft["id"],
        QuestConfirm(
            expected_state_version=draft["state_version"],
            expected_contract_version=1,
        ),
    )
    milestones = [
        {
            **item,
            "status": (
                "completed"
                if item["tool_name"] in {"list_directory", "write_file"}
                else item["status"]
            ),
        }
        for item in planned["milestones"]
    ]
    completed_event = storage.append_event(
        draft["id"],
        "MilestoneVerified",
        {"milestones": milestones},
        planned["state_version"],
    )
    storage.append_event(
        draft["id"],
        "UserDecisionRequested",
        {"status": "waiting_user"},
        completed_event["state_version_after"],
    )
    waiting = service.get_quest(draft["id"])
    before_state = waiting
    before_events = service.get_events(draft["id"])
    before_decisions = service.get_decisions(draft["id"])

    with pytest.raises(AppError) as caught:
        service.submit_decision(
            draft["id"],
            DecisionCreate(
                kind="modify",
                expected_state_version=waiting["state_version"],
                note="This would rewrite a completed write milestone",
                contract_patch={"goal": "Create a different completed brief"},
            ),
        )

    assert caught.value.code == "COMPLETED_MILESTONE_IMMUTABLE"
    assert caught.value.status_code == 409
    assert service.get_quest(draft["id"]) == before_state
    assert service.get_events(draft["id"]) == before_events
    assert service.get_decisions(draft["id"]) == before_decisions
