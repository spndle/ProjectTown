from __future__ import annotations

import hashlib
import json
import sqlite3
import threading

import pytest

from backend.app.runtime import stable_hash
from backend.app.v1.storage import V1Storage


def draft(s: V1Storage, q: str = "q1"):
    return s.create_draft(
        q,
        {"id": f"contract-{q}", "goal": "test goal", "version": 1},
        {
            "id": f"plan-{q}",
            "version": 1,
            "milestones": [
                {"id": "a"},
                {"id": "b", "depends_on": ["a"]},
            ],
        },
        workspace=f"quests/{q}",
    )


def test_migration_reentrant_and_cas(tmp_path):
    p = tmp_path / "v1.db"
    s = V1Storage(p)
    first = s.schema_versions()
    draft(s)
    event = s.append_event("q1", "Progressed", {"n": 1}, 1)
    assert event["state_version_after"] == 2
    assert s.get_quest("q1")["n"] == 1
    with pytest.raises(ValueError):
        s.append_event("q1", "Conflict", {}, 1)
    with pytest.raises(ValueError):
        s.append_event("q1", "Future", {}, 2, event_schema_version=99)
    s.close()
    s2 = V1Storage(p)
    assert s2.schema_versions() == first and len(s2.list_events("q1")) == 2


def test_replay_checkpoint_and_dag_rejection(tmp_path):
    s = V1Storage(tmp_path / "v1.db")
    draft(s)
    s.append_event("q1", "X", {"x": 1}, 1)
    s.save_checkpoint("q1")
    s.append_event("q1", "Y", {"y": 2}, 2)
    assert s.validate_checkpoint("q1")
    assert s.replay("q1") == s.replay("q1", True)
    with pytest.raises(ValueError):
        s.create_draft(
            "cycle",
            {"id": "c", "goal": "cycle goal"},
            {
                "milestones": [
                    {"id": "a", "depends_on": ["b"]},
                    {"id": "b", "depends_on": ["a"]},
                ]
            },
        )
    with pytest.raises(ValueError):
        s.create_draft(
            "missing",
            {"id": "m", "goal": "missing dependency"},
            {"milestones": [{"id": "a", "depends_on": ["z"]}]},
        )


def test_execution_admission_is_atomic_and_replayable(tmp_path):
    s = V1Storage(tmp_path / "v1.db")
    state = draft(s)
    state = s.append_event(
        "q1", "QuestConfirmed", {"status": "planned"}, state["state_version"]
    )
    state = s.get_quest("q1")
    assert state is not None
    event = s.admit_execution(
        "q1", "owner-1", 10, state["state_version"], "2026-01-01T00:00:00Z"
    )
    assert event is not None and event["event_type"] == "ExecutionAdmitted"
    current = s.get_quest("q1")
    assert (
        current["status"] == "running"
        and current["started_at"] == "2026-01-01T00:00:00Z"
    )
    assert s.replay("q1") == s.replay("q1", True) == current
    with pytest.raises(ValueError):
        s.admit_execution("q1", "owner-2", 10, state["state_version"], "later")
    assert (
        s._conn.execute("SELECT owner FROM v1_leases WHERE quest_id='q1'").fetchone()[0]
        == "owner-1"
    )


def test_execution_lease_owner_renewal_and_aba_are_safe(tmp_path):
    s = V1Storage(tmp_path / "v1.db")
    state = draft(s)
    s.append_event(
        "q1", "QuestConfirmed", {"status": "planned"}, state["state_version"]
    )
    state = s.get_quest("q1")
    assert state is not None
    assert (
        s.admit_execution("q1", "old", 10, state["state_version"], "admitted")
        is not None
    )
    assert s.renew_lease("q1", "old", 20)
    assert not s.renew_lease("q1", "other", 20)
    s._conn.execute("UPDATE v1_leases SET expires_at=0 WHERE quest_id='q1'")
    state = s.get_quest("q1")
    assert state is not None
    assert (
        s.admit_execution("q1", "new", 10, state["state_version"], "later") is not None
    )
    assert not s.renew_lease("q1", "old", 20)
    assert not s.release_lease("q1", "old")
    assert s.renew_lease("q1", "new", 20)


def test_action_idempotency_and_state_machine(tmp_path):
    s = V1Storage(tmp_path / "v1.db")
    draft(s)
    prepared = s.prepare_action(
        "action-1",
        "q1",
        "a",
        "q1:a:1",
        "write_file",
        "hash-one",
        {"path": "a.txt", "content": "one"},
        1,
        "absent",
    )
    duplicate = s.prepare_action(
        "different-action-id",
        "q1",
        "a",
        "q1:a:1",
        "write_file",
        "hash-one",
        {"path": "a.txt", "content": "one"},
        1,
        "absent",
    )
    assert duplicate["action_id"] == prepared["action_id"]
    with pytest.raises(ValueError):
        s.prepare_action(
            "action-2",
            "q1",
            "a",
            "q1:a:1",
            "write_file",
            "different-hash",
            {"path": "a.txt", "content": "two"},
            1,
        )
    assert s.mark_action_dispatched("action-1")["status"] == "dispatched"
    assert s.mark_action_unknown("action-1")["status"] == "unknown_effect"
    assert s.commit_action("action-1", {"path": "a.txt"})["status"] == "committed"


def test_atomic_tool_commit_is_idempotent_and_links_one_event(tmp_path):
    s = V1Storage(tmp_path / "v1.db")
    draft(s)
    s.prepare_action(
        "action-1",
        "q1",
        "a",
        "q1:a:1",
        "write_file",
        "hash-one",
        {"path": "a.txt", "content": "one"},
        1,
        "absent",
    )
    s.mark_action_dispatched("action-1")
    first = s.commit_action_with_event("action-1", {"path": "a.txt"})
    events = [
        event for event in s.list_events("q1") if event["event_type"] == "ToolCommitted"
    ]
    assert first["committed_event_id"] == events[0]["id"]
    assert (
        s.commit_action_with_event("action-1", {"path": "a.txt"})["committed_event_id"]
        == events[0]["id"]
    )
    assert (
        len(
            [
                event
                for event in s.list_events("q1")
                if event["event_type"] == "ToolCommitted"
            ]
        )
        == 1
    )
    with pytest.raises(ValueError, match="result conflict"):
        s.commit_action_with_event("action-1", {"path": "other.txt"})


def test_concurrent_decision_has_one_winner_and_replay_matches(tmp_path):
    s = V1Storage(tmp_path / "v1.db")
    state = draft(s)
    waiting = s.append_event(
        "q1",
        "UserDecisionRequested",
        {"status": "waiting_user"},
        state["state_version"],
    )
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def decide(kind: str) -> None:
        barrier.wait()
        try:
            s.apply_decision(
                "q1",
                f"decision-{kind}",
                {"kind": kind},
                expected_state_version=waiting["state_version_after"],
                kind=kind,
                events=[("UserRejected", {"status": "failed"})],
            )
            outcomes.append("ok")
        except ValueError:
            outcomes.append("conflict")

    threads = [
        threading.Thread(target=decide, args=(kind,)) for kind in ("reject", "reject")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["conflict", "ok"]
    assert len(s.list_decisions("q1")) == 1
    assert s.replay("q1") == s.get_quest("q1")


def test_committed_action_repair_is_reopen_idempotent(tmp_path):
    path = tmp_path / "v1.db"
    s = V1Storage(path)
    draft(s)
    s.prepare_action(
        "action-1", "q1", "a", "q1:a:1", "write_file", "hash-one", {"path": "a.txt"}, 1
    )
    s.mark_action_dispatched("action-1")
    s.commit_action("action-1", {"path": "a.txt"})
    s.close()
    repaired = V1Storage(path)
    assert repaired.action_recovery_summary()["linked_committed_actions"] == 1
    assert (
        len(
            [
                event
                for event in repaired.list_events("q1")
                if event["event_type"] == "ToolCommitted"
            ]
        )
        == 1
    )
    repaired.close()
    reopened = V1Storage(path)
    assert (
        len(
            [
                event
                for event in reopened.list_events("q1")
                if event["event_type"] == "ToolCommitted"
            ]
        )
        == 1
    )


def test_modify_completed_milestone_rolls_back_every_ledger(tmp_path):
    s = V1Storage(tmp_path / "v1.db")
    state = draft(s)
    completed = [
        {**item, "status": "completed" if item["id"] == "a" else item["status"]}
        for item in state["milestones"]
    ]
    s.append_event(
        "q1", "MilestoneVerified", {"milestones": completed}, state["state_version"]
    )
    waiting = s.append_event(
        "q1", "UserDecisionRequested", {"status": "waiting_user"}, 2
    )
    before = (s.get_quest("q1"), len(s.list_decisions("q1")), len(s.list_events("q1")))
    bad_plan = {
        "id": "plan-q1",
        "version": 2,
        "milestones": [
            {"id": "a", "tool_name": "read_file"},
            {"id": "b", "depends_on": ["a"]},
        ],
    }
    with pytest.raises(ValueError, match="completed"):
        s.apply_decision(
            "q1",
            "decision-modify",
            {"kind": "modify"},
            expected_state_version=waiting["state_version_after"],
            kind="modify",
            contract={"id": "contract-q1", "goal": "test goal", "version": 2},
            plan=bad_plan,
            events=[
                ("GoalContractModified", {"status": "replanning"}),
                ("PlanReplanned", {"status": "running"}),
                ("UserModificationApplied", {"status": "paused"}),
            ],
        )
    assert (
        s.get_quest("q1"),
        len(s.list_decisions("q1")),
        len(s.list_events("q1")),
    ) == before


def test_legacy_is_unverified(tmp_path):
    s = V1Storage(tmp_path / "v1.db")
    assert (
        s.import_legacy(
            [{"id": "old", "status": "completed"}, {"id": "run", "status": "running"}]
        )
        == 2
    )
    assert s.get_quest("old")["legacy_unverified"] is True
    assert s.get_quest("run")["status"] == "paused"
    assert s.get_quest("run")["recovery_required"] is True


def test_search_quests_filters_casefolded_id_or_contract_goal_and_paginates(tmp_path):
    s = V1Storage(tmp_path / "v1.db")
    draft(s, "q-alpha")
    s.create_draft(
        "q-beta",
        {"id": "contract-q-beta", "goal": "Unicode STRASSE", "version": 1},
        {"id": "plan-q-beta", "version": 1, "milestones": [{"id": "a"}]},
        workspace="quests/q-beta",
        status="planned",
    )
    s.create_draft(
        "q-gamma",
        {"id": "contract-q-gamma", "goal": "Unrelated", "version": 1},
        {"id": "plan-q-gamma", "version": 1, "milestones": [{"id": "a"}]},
        workspace="quests/q-gamma",
        status="failed",
    )

    by_id, id_total = s.search_quests(q="ALPHA", statuses=[], offset=0, limit=None)
    assert [state["id"] for state in by_id] == ["q-alpha"]
    assert id_total == 1
    by_goal, goal_total = s.search_quests(
        q="stra\u00dfe", statuses=["planned", "failed"], offset=0, limit=1
    )
    assert [state["id"] for state in by_goal] == ["q-beta"]
    assert goal_total == 1
    empty, empty_total = s.search_quests(q="missing", statuses=[], offset=0, limit=None)
    assert empty == []
    assert empty_total == 0
    page, page_total = s.search_quests(q=None, statuses=[], offset=1, limit=1)
    assert page_total == 3
    assert len(page) == 1


def test_event_and_evidence_ledgers_reject_update_and_delete(tmp_path):
    path = tmp_path / "v1.db"
    storage = V1Storage(path)
    draft(storage)
    storage.append_evidence(
        "q1",
        "evidence-1",
        {"criterion_id": "criterion-1", "passed": True},
    )
    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.DatabaseError, match="v1_events is immutable"):
            connection.execute(
                "UPDATE v1_events SET event_type = 'tampered' WHERE quest_id = 'q1'"
            )
        with pytest.raises(sqlite3.DatabaseError, match="v1_evidence is immutable"):
            connection.execute("DELETE FROM v1_evidence WHERE id = 'evidence-1'")
    assert storage.list_events("q1")[0]["event_type"] == "QuestDrafted"
    assert storage.list_evidence("q1")[0]["id"] == "evidence-1"
    storage.close()


def _complete_snapshot() -> dict[str, object]:
    entries = [
        {
            "relative_path": ".hidden",
            "file_type": "regular",
            "size": 3,
            "sha256": "b" * 64,
        }
    ]
    root_hash = hashlib.sha256(
        json.dumps(
            {"policy_version": "workspace-snapshot-v1", "entries": entries},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "workspace": "quests/q1",
        "policy_version": "workspace-snapshot-v1",
        "status": "complete",
        "root_hash": root_hash,
        "file_count": 1,
        "total_bytes": 3,
        "entries": entries,
    }


def test_baseline_snapshot_requires_live_owner_current_state_and_precedes_actions(
    tmp_path,
):
    storage = V1Storage(tmp_path / "provenance.db")
    state = draft(storage)
    state = storage.append_event(
        "q1", "QuestConfirmed", {"status": "planned"}, state["state_version"]
    )
    assert (
        storage.admit_execution(
            "q1", "owner", 10, state["state_version_after"], "admitted"
        )
        is not None
    )
    current = storage.get_quest("q1")
    assert current is not None
    with pytest.raises(ValueError, match="live execution lease"):
        storage.save_baseline_snapshot(
            "baseline-wrong-owner",
            "q1",
            "other",
            current["state_version"],
            _complete_snapshot(),
        )
    with pytest.raises(ValueError, match="state version conflict"):
        storage.save_baseline_snapshot(
            "baseline-wrong-state",
            "q1",
            "owner",
            current["state_version"] - 1,
            _complete_snapshot(),
        )
    saved = storage.save_baseline_snapshot(
        "baseline",
        "q1",
        "owner",
        current["state_version"],
        _complete_snapshot(),
        event_sequence=current["state_version"],
    )
    assert saved["kind"] == "baseline"
    with pytest.raises(ValueError, match="already exists"):
        storage.save_baseline_snapshot(
            "baseline-duplicate",
            "q1",
            "owner",
            current["state_version"],
            _complete_snapshot(),
        )
    with pytest.raises(sqlite3.IntegrityError, match="v1_workspace_snapshots.quest_id"):
        storage._conn.execute(
            """
            INSERT INTO v1_workspace_snapshots(
                snapshot_id, quest_id, kind, policy_version, workspace, root_hash,
                file_count, total_bytes, status, state_version, event_sequence, created_at
            ) VALUES ('baseline-db-duplicate', 'q1', 'baseline', 'policy', 'quests/q1',
                NULL, 0, 0, 'unsupported', NULL, NULL, 'now')
            """
        )
    assert storage.list_workspace_snapshot_entries("baseline") == [
        {
            "relative_path": ".hidden",
            "file_type": "regular",
            "size": 3,
            "sha256": "b" * 64,
        }
    ]
    storage.prepare_action(
        "action",
        "q1",
        "a",
        "q1:a:1",
        "write_text",
        "arguments",
        {"path": "x.txt"},
        current["state_version"],
    )
    with pytest.raises(ValueError, match="precede every tool action"):
        storage.save_baseline_snapshot(
            "baseline-late",
            "q1",
            "owner",
            current["state_version"],
            _complete_snapshot(),
        )


def test_workspace_snapshot_insert_is_atomic_and_final_is_immutable(tmp_path):
    storage = V1Storage(tmp_path / "provenance.db")
    draft(storage)
    bad = _complete_snapshot()
    bad["entries"] = [
        {
            "relative_path": "../outside",
            "file_type": "regular",
            "size": 3,
            "sha256": "b" * 64,
        }
    ]
    with pytest.raises(ValueError, match="path"):
        storage.save_final_snapshot("failed-final", "q1", bad)
    assert storage.get_workspace_snapshot("failed-final") is None
    saved = storage.save_final_snapshot(
        "final", "q1", _complete_snapshot(), state_version=1, event_sequence=1
    )
    assert saved["kind"] == "final"
    assert [
        snapshot["snapshot_id"] for snapshot in storage.list_workspace_snapshots("q1")
    ] == ["final"]
    with pytest.raises(
        sqlite3.DatabaseError, match="v1_workspace_snapshots is immutable"
    ):
        storage._conn.execute(
            "UPDATE v1_workspace_snapshots SET status='unsupported' WHERE snapshot_id='final'"
        )


def test_tool_file_observation_has_one_row_per_action(tmp_path):
    storage = V1Storage(tmp_path / "provenance.db")
    draft(storage)
    storage.prepare_action(
        "action",
        "q1",
        "a",
        "q1:a:1",
        "write_text",
        "arguments",
        {"path": "x.txt"},
        1,
    )
    storage.mark_action_dispatched("action")
    committed = storage.commit_action_with_event("action", {"path": "x.txt"})
    event_id = committed["committed_event_id"]
    assert event_id is not None
    with pytest.raises(ValueError, match="tool file observation is invalid"):
        storage.append_tool_file_observation(
            "null-event",
            "q1",
            "action",
            "x.txt",
            committed_event_id=None,
            before_sha256=None,
            after_sha256="a" * 64,
            after_size_bytes=1,
            change_kind="created",
            status="observed",
        )
    with pytest.raises(ValueError, match="action/event binding"):
        storage.append_tool_file_observation(
            "wrong-event",
            "q1",
            "action",
            "x.txt",
            committed_event_id=1,
            before_sha256=None,
            after_sha256="a" * 64,
            after_size_bytes=1,
            change_kind="created",
            status="observed",
        )
    storage.create_draft(
        "q2",
        {"id": "contract-q2", "goal": "test", "version": 1},
        {"id": "plan-q2", "version": 1, "milestones": [{"id": "a"}]},
        workspace="quests/q2",
    )
    with pytest.raises(ValueError, match="does not belong"):
        storage.append_tool_file_observation(
            "wrong-quest",
            "q2",
            "action",
            "x.txt",
            committed_event_id=event_id,
            before_sha256=None,
            after_sha256="a" * 64,
            after_size_bytes=1,
            change_kind="created",
            status="observed",
        )
    storage.append_tool_file_observation(
        "observation-1",
        "q1",
        "action",
        "x.txt",
        committed_event_id=event_id,
        before_sha256=None,
        after_sha256="a" * 64,
        after_size_bytes=1,
        change_kind="created",
        status="observed",
    )
    with pytest.raises(
        sqlite3.IntegrityError,
        match="v1_tool_file_observations.action_id",
    ):
        storage.append_tool_file_observation(
            "observation-2",
            "q1",
            "action",
            "other.txt",
            committed_event_id=event_id,
            before_sha256=None,
            after_sha256="b" * 64,
            after_size_bytes=1,
            change_kind="created",
            status="observed",
        )


def test_atomic_commit_rolls_back_action_event_and_observation_on_insert_failure(
    tmp_path, monkeypatch
):
    storage = V1Storage(tmp_path / "provenance.db")
    draft(storage)
    storage.prepare_action(
        "action",
        "q1",
        "a",
        "q1:a:1",
        "write_file",
        "arguments",
        {"path": "x.txt"},
        1,
        "absent",
    )
    storage.mark_action_dispatched("action")

    def fail_observation(*_args, **_kwargs):
        raise RuntimeError("injected observation persistence failure")

    monkeypatch.setattr(
        storage, "_insert_tool_file_observation_locked", fail_observation
    )
    with pytest.raises(RuntimeError, match="injected observation"):
        storage.commit_action_with_event(
            "action",
            {"path": "x.txt"},
            file_observation={
                "observation_id": "observation",
                "relative_path": "x.txt",
                "before_sha256": None,
                "after_sha256": "a" * 64,
                "after_size_bytes": 1,
                "change_kind": "created",
                "status": "observed",
            },
        )
    assert storage.get_action("action")["status"] == "dispatched"
    assert not [
        event
        for event in storage.list_events("q1")
        if event["event_type"] == "ToolCommitted"
    ]
    assert (
        storage._conn.execute(
            "SELECT COUNT(*) FROM v1_tool_file_observations"
        ).fetchone()[0]
        == 0
    )


def _artifact_provenance_fixture(
    storage: V1Storage,
) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]], int]:
    state = draft(storage)
    state = storage.append_event(
        "q1", "QuestConfirmed", {"status": "planned"}, state["state_version"]
    )
    assert storage.admit_execution(
        "q1", "owner", 10, state["state_version_after"], "admitted"
    )
    state = storage.get_quest("q1")
    assert state is not None
    storage.save_baseline_snapshot(
        "baseline",
        "q1",
        "owner",
        state["state_version"],
        _complete_snapshot(),
        event_sequence=state["state_version"],
    )
    storage.prepare_action(
        "artifact-action",
        "q1",
        "a",
        "q1:a:artifact",
        "write_file",
        "arguments",
        {"path": ".hidden"},
        state["state_version"],
        "absent",
    )
    storage.mark_action_dispatched("artifact-action")
    committed = storage.commit_action_with_event(
        "artifact-action",
        {"path": ".hidden"},
        file_observation={
            "observation_id": "artifact-observation",
            "relative_path": ".hidden",
            "before_sha256": None,
            "after_sha256": "b" * 64,
            "after_size_bytes": 3,
            "change_kind": "created",
            "status": "observed",
        },
    )
    state = storage.get_quest("q1")
    assert state is not None
    storage.append_evidence("q1", "evidence-artifact", {"artifact_path": ".hidden"})
    manifest = [
        {
            "artifact_id": "artifact-1",
            "provenance_id": "provenance-1",
            "path": ".hidden",
            "hash": "b" * 64,
            "size": 3,
            "evidence_id": "evidence-artifact",
            "baseline_snapshot_id": "baseline",
            "final_snapshot_id": "final-1",
            "provenance_mode": "compatibility_shadow",
            "provenance_status": "shadow_observed_created",
            "created_by_quest": True,
        }
    ]
    provenance = [
        {
            "provenance_id": "provenance-1",
            "artifact_id": "artifact-1",
            "path": ".hidden",
            "artifact_hash": "b" * 64,
            "evidence_id": "evidence-artifact",
            "baseline_snapshot_id": "baseline",
            "final_snapshot_id": "final-1",
            "status": "shadow",
            "provenance_mode": "compatibility_shadow",
            "provenance_status": "shadow_observed_created",
            "action_id": "artifact-action",
            "committed_event_id": committed["committed_event_id"],
        }
    ]
    return _complete_snapshot(), manifest, provenance, state["state_version"]


def test_legacy_baseline_and_provenance_queries_are_guarded_and_deterministic(tmp_path):
    storage = V1Storage(tmp_path / "provenance.db")
    snapshot, manifest, provenance, state_version = _artifact_provenance_fixture(
        storage
    )
    baseline = storage.get_baseline_snapshot("q1")
    assert baseline is not None and baseline["status"] == "complete"
    assert [action["action_id"] for action in storage.list_tool_actions("q1")] == [
        "artifact-action"
    ]
    observations = storage.list_tool_file_observations("q1")
    assert len(observations) == 1 and observations[0]["committed_event_sequence"] > 0
    event = storage.request_artifact_review_with_provenance(
        "q1",
        owner="owner",
        review_id="review-1",
        manifest=manifest,
        manifest_hash=stable_hash(manifest),
        final_snapshot_id="final-1",
        final_snapshot=snapshot,
        provenance=provenance,
        expected_state_version=state_version,
    )
    assert event["event_type"] == "ArtifactReviewRequested"
    assert (
        storage.get_workspace_snapshot("final-1")["event_sequence"] == state_version + 1
    )
    assert storage.list_artifact_provenance("q1")[0]["artifact_id"] == "artifact-1"
    assert storage.get_quest("q1")["pending_artifact_review"]["item_count"] == 1


def test_artifact_provenance_request_rejects_forgery_and_rolls_back(
    tmp_path, monkeypatch
):
    storage = V1Storage(tmp_path / "provenance.db")
    snapshot, manifest, provenance, state_version = _artifact_provenance_fixture(
        storage
    )
    with pytest.raises(ValueError, match="manifest binding"):
        storage.request_artifact_review_with_provenance(
            "q1",
            owner="owner",
            review_id="bad",
            manifest=manifest,
            manifest_hash="a" * 64,
            final_snapshot_id="final-1",
            final_snapshot=snapshot,
            provenance=provenance,
            expected_state_version=state_version,
        )
    forged = [dict(provenance[0], status="verified")]
    with pytest.raises(ValueError, match="artifact provenance"):
        storage.request_artifact_review_with_provenance(
            "q1",
            owner="owner",
            review_id="verified",
            manifest=manifest,
            manifest_hash=stable_hash(manifest),
            final_snapshot_id="final-1",
            final_snapshot=snapshot,
            provenance=forged,
            expected_state_version=state_version,
        )
    mismatch = [dict(provenance[0], action_id=None, committed_event_id=None)]
    with pytest.raises(ValueError, match="observed"):
        storage.request_artifact_review_with_provenance(
            "q1",
            owner="owner",
            review_id="missing-observation",
            manifest=manifest,
            manifest_hash=stable_hash(manifest),
            final_snapshot_id="final-1",
            final_snapshot=snapshot,
            provenance=mismatch,
            expected_state_version=state_version,
        )

    def fail_insert(*_args, **_kwargs):
        raise RuntimeError("injected provenance failure")

    monkeypatch.setattr(storage, "_insert_artifact_provenance_locked", fail_insert)
    with pytest.raises(RuntimeError, match="injected provenance"):
        storage.request_artifact_review_with_provenance(
            "q1",
            owner="owner",
            review_id="rollback",
            manifest=manifest,
            manifest_hash=stable_hash(manifest),
            final_snapshot_id="final-1",
            final_snapshot=snapshot,
            provenance=provenance,
            expected_state_version=state_version,
        )
    assert storage.get_workspace_snapshot("final-1") is None
    assert storage.list_artifact_provenance("q1") == []
    assert not [
        event
        for event in storage.list_events("q1")
        if event["event_type"] == "ArtifactReviewRequested"
    ]


def test_artifact_review_manifest_identity_status_and_lease_are_fail_closed(tmp_path):
    storage = V1Storage(tmp_path / "provenance.db")
    snapshot, manifest, provenance, state_version = _artifact_provenance_fixture(
        storage
    )

    def request(
        *,
        owner="owner",
        items=manifest,
        rows=provenance,
        snapshot_id="final-1",
    ):
        return storage.request_artifact_review_with_provenance(
            "q1",
            owner=owner,
            review_id="negative",
            manifest=items,
            manifest_hash=stable_hash(items),
            final_snapshot_id=snapshot_id,
            final_snapshot=snapshot,
            provenance=rows,
            expected_state_version=state_version,
        )

    with pytest.raises(ValueError, match="lease"):
        request(owner="wrong-owner")
    with pytest.raises(ValueError, match="match manifest"):
        request(items=[dict(manifest[0], provenance_id="wrong-id")])
    missing_provenance_id = dict(manifest[0])
    del missing_provenance_id["provenance_id"]
    with pytest.raises(ValueError, match="identity"):
        request(items=[missing_provenance_id])
    with pytest.raises(ValueError, match="match manifest"):
        request(items=[dict(manifest[0], provenance_status="shadow_external_drift")])
    with pytest.raises(ValueError, match="item"):
        request(
            items=[dict(manifest[0], provenance_mode="untrusted")],
            rows=[dict(provenance[0], provenance_mode="untrusted")],
        )
    with pytest.raises(ValueError, match="invalid"):
        request(items=[dict(manifest[0], final_snapshot_id="wrong-final")])
    with pytest.raises(ValueError, match="identity"):
        request(items=[dict(manifest[0], path="../escape")])
    duplicate = dict(
        manifest[0], artifact_id="artifact-2", provenance_id="provenance-2"
    )
    duplicate_row = dict(
        provenance[0], artifact_id="artifact-2", provenance_id="provenance-2"
    )
    with pytest.raises(ValueError, match="identity"):
        request(items=[manifest[0], duplicate], rows=[provenance[0], duplicate_row])
    with pytest.raises(ValueError, match="item"):
        request(items=[dict(manifest[0], size=True)])
    with pytest.raises(ValueError, match="item"):
        request(items=[dict(manifest[0], size=-1)])
    unrecoverable_manifest = [
        dict(manifest[0], provenance_status="unrecoverable_unknown")
    ]
    unrecoverable_provenance = [
        dict(
            provenance[0],
            provenance_status="unrecoverable_unknown",
            status="unrecoverable",
            action_id=None,
            committed_event_id=None,
        )
    ]
    with pytest.raises(ValueError, match="status mapping"):
        request(items=unrecoverable_manifest, rows=unrecoverable_provenance)

    storage._conn.execute(
        "UPDATE v1_leases SET expires_at = 0 WHERE quest_id = ?", ("q1",)
    )
    with pytest.raises(ValueError, match="lease"):
        request()


def test_snapshot_workspace_and_execution_admission_bindings_are_exact(tmp_path):
    storage = V1Storage(tmp_path / "bindings.db")
    state = draft(storage)
    confirmed = storage.append_event(
        "q1", "QuestConfirmed", {"status": "planned"}, state["state_version"]
    )
    assert storage.admit_execution(
        "q1", "owner", 10, confirmed["state_version_after"], "admitted"
    )
    state = storage.require_quest("q1")
    wrong_workspace = dict(_complete_snapshot(), workspace="quests/other")
    with pytest.raises(ValueError, match="workspace"):
        storage.save_baseline_snapshot(
            "wrong-workspace",
            "q1",
            "owner",
            state["state_version"],
            wrong_workspace,
            event_sequence=state["state_version"],
        )
    with pytest.raises(ValueError, match="execution admission"):
        storage.save_baseline_snapshot(
            "wrong-event",
            "q1",
            "owner",
            state["state_version"],
            _complete_snapshot(),
            event_sequence=1,
        )
    with pytest.raises(ValueError, match="event sequence"):
        storage.save_baseline_snapshot(
            "bool-event",
            "q1",
            "owner",
            state["state_version"],
            _complete_snapshot(),
            event_sequence=True,
        )
    with pytest.raises(ValueError, match="workspace"):
        storage.save_final_snapshot("wrong-final", "q1", wrong_workspace)

    storage.prepare_action(
        "legacy-action",
        "q1",
        "a",
        "q1:a:legacy",
        "write_file",
        "arguments",
        {"path": "legacy.txt"},
        state["state_version"],
        "absent",
    )
    legacy = storage.record_legacy_unobserved_baseline(
        "legacy-baseline",
        "q1",
        "owner",
        state["state_version"],
        event_sequence=state["state_version"],
    )
    assert legacy["status"] == "legacy_unobserved"


def test_artifact_review_accepts_classifier_unrecoverable_results_only(tmp_path):
    def snapshot_with_entries(entries):
        root_hash = hashlib.sha256(
            json.dumps(
                {"policy_version": "workspace-snapshot-v1", "entries": entries},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return {
            "workspace": "quests/q1",
            "policy_version": "workspace-snapshot-v1",
            "status": "complete",
            "root_hash": root_hash,
            "file_count": len(entries),
            "total_bytes": sum(entry["size"] for entry in entries),
            "entries": entries,
        }

    cases = {
        "unrecoverable_final_missing": snapshot_with_entries([]),
        "unrecoverable_final_hash_mismatch": snapshot_with_entries(
            [dict(_complete_snapshot()["entries"][0], sha256="c" * 64)]
        ),
        "unrecoverable_final_size_mismatch": snapshot_with_entries(
            [dict(_complete_snapshot()["entries"][0], size=4)]
        ),
        "unrecoverable_final_unstable": {
            "workspace": "quests/q1",
            "policy_version": "workspace-snapshot-v1",
            "status": "unstable",
            "root_hash": None,
            "file_count": 0,
            "total_bytes": 0,
            "entries": [],
        },
        "unrecoverable_chain_break": _complete_snapshot(),
    }
    for index, (provenance_status, final_snapshot) in enumerate(cases.items()):
        storage = V1Storage(tmp_path / f"classifier-{index}.db")
        _, manifest, provenance, state_version = _artifact_provenance_fixture(storage)
        final_snapshot_id = f"final-{index}"
        manifest = [
            dict(
                manifest[0],
                final_snapshot_id=final_snapshot_id,
                provenance_status=provenance_status,
            )
        ]
        provenance = [
            dict(
                provenance[0],
                final_snapshot_id=final_snapshot_id,
                status="unrecoverable",
                provenance_status=provenance_status,
                action_id=None,
                committed_event_id=None,
            )
        ]
        event = storage.request_artifact_review_with_provenance(
            "q1",
            owner="owner",
            review_id=f"review-{index}",
            manifest=manifest,
            manifest_hash=stable_hash(manifest),
            final_snapshot_id=final_snapshot_id,
            final_snapshot=final_snapshot,
            provenance=provenance,
            expected_state_version=state_version,
        )
        assert event["event_type"] == "ArtifactReviewRequested"

    storage = V1Storage(tmp_path / "classifier-shadow.db")
    _, manifest, provenance, state_version = _artifact_provenance_fixture(storage)
    mismatch_snapshot = snapshot_with_entries([])
    manifest = [dict(manifest[0], final_snapshot_id="shadow-final")]
    provenance = [dict(provenance[0], final_snapshot_id="shadow-final")]
    with pytest.raises(ValueError, match="does not match final snapshot"):
        storage.request_artifact_review_with_provenance(
            "q1",
            owner="owner",
            review_id="shadow-mismatch",
            manifest=manifest,
            manifest_hash=stable_hash(manifest),
            final_snapshot_id="shadow-final",
            final_snapshot=mismatch_snapshot,
            provenance=provenance,
            expected_state_version=state_version,
        )


def test_artifact_review_rejects_workspace_and_snapshot_status_mismatch(tmp_path):
    storage = V1Storage(tmp_path / "review-bindings.db")
    snapshot, manifest, provenance, state_version = _artifact_provenance_fixture(
        storage
    )
    with pytest.raises(ValueError, match="workspace"):
        storage.request_artifact_review_with_provenance(
            "q1",
            owner="owner",
            review_id="wrong-workspace",
            manifest=manifest,
            manifest_hash=stable_hash(manifest),
            final_snapshot_id="final-1",
            final_snapshot=dict(snapshot, workspace="quests/other"),
            provenance=provenance,
            expected_state_version=state_version,
        )
    unsupported = {
        "workspace": "quests/q1",
        "policy_version": "workspace-snapshot-v1",
        "status": "unsupported",
        "root_hash": None,
        "file_count": 0,
        "total_bytes": 0,
        "entries": [],
    }
    with pytest.raises(ValueError, match="complete snapshots"):
        storage.request_artifact_review_with_provenance(
            "q1",
            owner="owner",
            review_id="shadow-incomplete",
            manifest=manifest,
            manifest_hash=stable_hash(manifest),
            final_snapshot_id="final-1",
            final_snapshot=unsupported,
            provenance=provenance,
            expected_state_version=state_version,
        )
