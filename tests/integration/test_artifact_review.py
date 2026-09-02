from __future__ import annotations

import copy
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from backend.app.agent import RuleBasedAgent
from backend.app.errors import AppError
from backend.app.tools import Sandbox, build_default_registry
from backend.app.v1.gateway import ToolGateway
from backend.app.v1.provenance import WorkspaceSnapshot
from backend.app.v1.service import V1QuestService
from backend.app.v1.storage import V1Storage
from tests.conftest import APIContext


def _wait_for_status(
    api: APIContext, quest_id: str, expected: set[str], timeout: float = 5.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    latest: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        response = api.client.get(f"/api/v2/quests/{quest_id}")
        assert response.status_code == 200, response.text
        latest = response.json()
        if latest["status"] in expected:
            return latest
        time.sleep(0.01)
    pytest.fail(f"Quest {quest_id!r} did not reach {expected!r}: {latest!r}")


def _start_review_quest(api: APIContext) -> dict[str, Any]:
    created = api.client.post(
        "/api/v2/quests",
        json={
            "goal": "Create a reviewable project brief",
            "template_id": "project_brief",
            "artifact_review_required": True,
        },
    )
    assert created.status_code == 201, created.text
    draft = created.json()
    assert draft["status"] == "draft"
    assert draft["workspace"].startswith("quests/qv1_")
    assert draft["artifact_review_required"] is True

    confirmed = api.client.post(
        f"/api/v2/quests/{draft['id']}/confirm",
        json={
            "expected_state_version": draft["state_version"],
            "expected_contract_version": 1,
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    planned = confirmed.json()
    started = api.client.post(
        f"/api/v2/quests/{draft['id']}/run",
        json={"expected_state_version": planned["state_version"]},
    )
    assert started.status_code == 202, started.text
    return _wait_for_status(api, draft["id"], {"waiting_user", "failed"})


def _pending_review(state: dict[str, Any]) -> tuple[str, str]:
    assert state["status"] == "waiting_user", state.get("error")
    pending = state["pending_artifact_review"]
    assert pending, "successful opt-in execution must wait for user artifact review"
    review_id = pending["review_id"]
    manifest_hash = pending["manifest_hash"]
    assert isinstance(review_id, str) and review_id
    assert isinstance(manifest_hash, str) and len(manifest_hash) == 64
    return review_id, manifest_hash


def _review_payload(
    state: dict[str, Any], *, decision: str, key: str
) -> dict[str, Any]:
    review_id, manifest_hash = _pending_review(state)
    return {
        "expected_state_version": state["state_version"],
        "review_id": review_id,
        "manifest_hash": manifest_hash,
        "decision": decision,
        "idempotency_key": key,
    }


def test_default_quest_remains_backward_compatible_and_completes(api: APIContext):
    created = api.client.post(
        "/api/v2/quests",
        json={
            "goal": "Create a normal backwards compatible brief",
            "template_id": "project_brief",
            "workspace": "quests/default-artifact-compat",
        },
    )
    assert created.status_code == 201, created.text
    draft = created.json()
    assert draft["artifact_review_required"] is False
    confirmed = api.client.post(
        f"/api/v2/quests/{draft['id']}/confirm",
        json={"expected_state_version": 1, "expected_contract_version": 1},
    )
    assert confirmed.status_code == 200, confirmed.text
    started = api.client.post(
        f"/api/v2/quests/{draft['id']}/run",
        json={"expected_state_version": confirmed.json()["state_version"]},
    )
    assert started.status_code == 202, started.text
    completed = _wait_for_status(api, draft["id"], {"completed", "failed"})
    assert completed["status"] == "completed", completed.get("error")


def test_opt_in_rejects_explicit_workspace(api: APIContext):
    response = api.client.post(
        "/api/v2/quests",
        json={
            "goal": "Artifact review must own its workspace",
            "template_id": "project_brief",
            "workspace": "quests/shared-space",
            "artifact_review_required": True,
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "ARTIFACT_REVIEW_WORKSPACE_FORBIDDEN"


def test_identical_review_quests_have_isolated_verification_evidence(
    api: APIContext,
):
    """Evidence primary keys must be stable per Quest, not global per artifact."""

    first = _start_review_quest(api)
    second = _start_review_quest(api)
    _pending_review(first)
    _pending_review(second)

    assert first["id"] != second["id"]
    first_evidence = api.client.get(f"/api/v2/quests/{first['id']}/evidence")
    second_evidence = api.client.get(f"/api/v2/quests/{second['id']}/evidence")
    assert first_evidence.status_code == 200, first_evidence.text
    assert second_evidence.status_code == 200, second_evidence.text
    first_ids = {item["id"] for item in first_evidence.json()["items"]}
    second_ids = {item["id"] for item in second_evidence.json()["items"]}
    assert first_ids and second_ids
    assert first_ids.isdisjoint(second_ids)

    for state in (first, second):
        artifacts = api.client.get(f"/api/v2/quests/{state['id']}/artifacts")
        assert artifacts.status_code == 200, artifacts.text
        item = artifacts.json()["items"][0]
        preview = api.client.get(
            f"/api/v2/quests/{state['id']}/artifacts/{item['artifact_id']}/preview"
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["content"].strip()


def test_preview_then_retain_is_idempotent_and_emits_completion(api: APIContext):
    state = _start_review_quest(api)
    payload = _review_payload(state, decision="retain", key="retain-once")

    artifacts = api.client.get(f"/api/v2/quests/{state['id']}/artifacts")
    assert artifacts.status_code == 200, artifacts.text
    items = artifacts.json()["items"]
    assert items
    preview = api.client.get(
        f"/api/v2/quests/{state['id']}/artifacts/{items[0]['artifact_id']}/preview"
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["content"].strip()
    assert preview.json()["hash"] == items[0]["hash"]

    accepted = api.client.post(
        f"/api/v2/quests/{state['id']}/artifacts/review", json=payload
    )
    assert accepted.status_code == 200, accepted.text
    completed = accepted.json()
    assert completed["status"] == "completed"
    assert completed["artifact_disposition"] == "retained"
    assert completed["pending_artifact_review"] is None

    retry = api.client.post(
        f"/api/v2/quests/{state['id']}/artifacts/review", json=payload
    )
    assert retry.status_code == 200, retry.text
    assert retry.json() == completed
    conflict = api.client.post(
        f"/api/v2/quests/{state['id']}/artifacts/review",
        json={**payload, "idempotency_key": "retain-different-key"},
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    events = api.client.get(f"/api/v2/quests/{state['id']}/events").json()["items"]
    names = [event["event_type"] for event in events]
    assert names.index("ArtifactAccepted") < names.index("QuestCompleted")


def test_review_persists_compatibility_shadow_provenance_and_retain(api: APIContext):
    state = _start_review_quest(api)
    storage = api.app.state.runtime_storage
    baseline = storage.get_baseline_snapshot(state["id"])
    snapshots = storage.list_workspace_snapshots(state["id"])
    observations = storage.list_tool_file_observations(state["id"])
    provenance = storage.list_artifact_provenance(state["id"])

    assert baseline is not None
    assert baseline["status"] == "complete"
    assert {snapshot["kind"] for snapshot in snapshots} == {"baseline", "final"}
    assert observations
    assert len(provenance) == len(state["artifact_manifest"])
    assert [event["event_type"] for event in storage.list_events(state["id"])].count(
        "ArtifactReviewRequested"
    ) == 1
    for item in state["artifact_manifest"]:
        assert item["provenance_mode"] == "compatibility_shadow"
        assert item["provenance_id"]
        assert item["baseline_snapshot_id"] == baseline["snapshot_id"]
        assert item["final_snapshot_id"]
        assert item["provenance_status"].startswith("shadow_observed_")

    retained = api.client.post(
        f"/api/v2/quests/{state['id']}/artifacts/review",
        json=_review_payload(state, decision="retain", key="shadow-retain"),
    )
    assert retained.status_code == 200, retained.text
    assert retained.json()["status"] == "completed"
    assert retained.json()["artifact_disposition"] == "retained"


def test_provenance_reference_is_optional_for_legacy_and_rejects_forgery(
    api: APIContext,
) -> None:
    state = _start_review_quest(api)
    service = api.app.state.runtime_service
    legacy = copy.deepcopy(state)
    for item in legacy["artifact_manifest"]:
        for key in (
            "provenance_id",
            "provenance_status",
            "provenance_reason_code",
            "provenance_mode",
            "baseline_snapshot_id",
            "final_snapshot_id",
        ):
            item.pop(key)
    service._verify_artifact_manifest(legacy)

    forged = copy.deepcopy(state)
    forged["artifact_manifest"][0]["provenance_id"] = "prov_missing"
    with pytest.raises(AppError) as exc_info:
        service._verify_artifact_manifest(forged)
    assert exc_info.value.code == "ARTIFACT_PROVENANCE_INVALID"
    assert service.get_quest(state["id"])["status"] == "waiting_user"


def test_incomplete_final_shadow_snapshot_keeps_review_pending(
    api: APIContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    import backend.app.v1.service as service_module

    original_scan = service_module.scan_sandbox_workspace
    calls = 0

    def unstable_final(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return original_scan(*args, **kwargs)
        return WorkspaceSnapshot(
            workspace=str(kwargs.get("workspace", args[1])),
            policy_version="workspace-snapshot-v1",
            status="unstable",
            entries=(),
            root_hash=None,
            file_count=0,
            total_bytes=0,
        )

    monkeypatch.setattr(service_module, "scan_sandbox_workspace", unstable_final)
    state = _start_review_quest(api)
    assert state["status"] == "waiting_user"
    final = next(
        item
        for item in api.app.state.runtime_storage.list_workspace_snapshots(state["id"])
        if item["kind"] == "final"
    )
    assert final["status"] == "unstable"
    assert all(
        item["provenance_status"].startswith("unrecoverable_final_")
        for item in state["artifact_manifest"]
    )


def test_missing_shadow_baseline_reaches_runtime_failure_boundary(
    api: APIContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = api.app.state.runtime_storage
    original_get_baseline = storage.get_baseline_snapshot

    def hide_only_final_baseline(quest_id: str):
        events = storage.list_events(quest_id)
        if any(event["event_type"] == "QuestVerificationStarted" for event in events):
            return None
        return original_get_baseline(quest_id)

    monkeypatch.setattr(storage, "get_baseline_snapshot", hide_only_final_baseline)
    state = _start_review_quest(api)
    assert state["status"] == "failed"
    assert state["error"]["code"] == "RUNTIME_ERROR"
    assert "ArtifactReviewRequested" not in {
        event["event_type"] for event in storage.list_events(state["id"])
    }


def test_review_structure_error_reaches_runtime_failure_boundary(
    api: APIContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = api.app.state.runtime_storage

    def malformed_review(*_args, **_kwargs):
        raise ValueError("artifact manifest binding is invalid")

    monkeypatch.setattr(
        storage, "request_artifact_review_with_provenance", malformed_review
    )
    state = _start_review_quest(api)
    assert state["status"] == "failed"
    assert state["error"]["code"] == "RUNTIME_ERROR"
    assert "ArtifactReviewRequested" not in {
        event["event_type"] for event in storage.list_events(state["id"])
    }


def test_discard_deletes_only_frozen_manifest_files_and_is_idempotent(api: APIContext):
    state = _start_review_quest(api)
    payload = _review_payload(state, decision="discard", key="discard-once")
    artifacts = api.client.get(f"/api/v2/quests/{state['id']}/artifacts").json()[
        "items"
    ]
    assert artifacts
    paths = [api.sandbox_root / state["workspace"] / item["path"] for item in artifacts]
    assert all(path.is_file() for path in paths)

    discarded = api.client.post(
        f"/api/v2/quests/{state['id']}/artifacts/review", json=payload
    )
    assert discarded.status_code == 200, discarded.text
    failed = discarded.json()
    assert failed["status"] == "failed"
    assert failed["artifact_disposition"] == "discarded"
    assert all(not path.exists() for path in paths)

    retry = api.client.post(
        f"/api/v2/quests/{state['id']}/artifacts/review", json=payload
    )
    assert retry.status_code == 200, retry.text
    assert retry.json() == failed


def test_tampered_artifact_is_not_deleted_and_stays_recoverable(api: APIContext):
    state = _start_review_quest(api)
    payload = _review_payload(state, decision="discard", key="tampered-discard")
    item = api.client.get(f"/api/v2/quests/{state['id']}/artifacts").json()["items"][0]
    path = api.sandbox_root / state["workspace"] / item["path"]
    path.write_text("tampered after review was frozen\n", encoding="utf-8")

    response = api.client.post(
        f"/api/v2/quests/{state['id']}/artifacts/review", json=payload
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] in {
        "ARTIFACT_CHANGED",
        "ARTIFACT_HASH_MISMATCH",
    }
    assert path.read_text(encoding="utf-8") == "tampered after review was frozen\n"
    after = api.client.get(f"/api/v2/quests/{state['id']}").json()
    assert after["status"] in {"waiting_user", "discarding"}
    assert after["status"] not in {"completed", "failed"}


def test_tampered_artifact_cannot_be_retained(api: APIContext):
    state = _start_review_quest(api)
    payload = _review_payload(state, decision="retain", key="tampered-retain")
    item = api.client.get(f"/api/v2/quests/{state['id']}/artifacts").json()["items"][0]
    path = api.sandbox_root / state["workspace"] / item["path"]
    path.write_text("changed after preview\n", encoding="utf-8")

    response = api.client.post(
        f"/api/v2/quests/{state['id']}/artifacts/review", json=payload
    )
    assert response.status_code == 409, response.text
    after = api.client.get(f"/api/v2/quests/{state['id']}").json()
    assert after["status"] == "waiting_user"
    assert after["artifact_disposition"] == "pending"


def test_review_identity_and_note_are_part_of_idempotency(api: APIContext):
    state = _start_review_quest(api)
    payload = _review_payload(state, decision="retain", key="review-identity")
    payload["note"] = "first note"
    accepted = api.client.post(
        f"/api/v2/quests/{state['id']}/artifacts/review", json=payload
    )
    assert accepted.status_code == 200, accepted.text
    changed_note = api.client.post(
        f"/api/v2/quests/{state['id']}/artifacts/review",
        json={**payload, "note": "different note"},
    )
    assert changed_note.status_code == 409, changed_note.text
    assert changed_note.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_generic_decision_cannot_bypass_artifact_review(api: APIContext):
    state = _start_review_quest(api)
    response = api.client.post(
        f"/api/v2/quests/{state['id']}/decisions",
        json={
            "kind": "reject",
            "expected_state_version": state["state_version"],
            "note": "must use artifact disposition",
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "ARTIFACT_REVIEW_REQUIRED"
    after = api.client.get(f"/api/v2/quests/{state['id']}").json()
    assert after["status"] == "waiting_user"


def test_concurrent_retain_and_discard_have_one_winner(api: APIContext):
    state = _start_review_quest(api)
    retain = _review_payload(state, decision="retain", key="race-retain")
    discard = _review_payload(state, decision="discard", key="race-discard")

    def submit(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        response = api.client.post(
            f"/api/v2/quests/{state['id']}/artifacts/review", json=payload
        )
        return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(submit, (retain, discard)))

    assert sorted(status for status, _ in responses) == [200, 409]
    final = api.client.get(f"/api/v2/quests/{state['id']}").json()
    assert final["artifact_disposition"] in {"retained", "discarded"}
    assert final["status"] in {"completed", "failed"}
    receipt = api.app.state.runtime_storage.get_artifact_review_receipt(state["id"])
    assert receipt is not None
    if receipt["decision"] == "retain":
        assert final["artifact_disposition"] == "retained"
    else:
        assert final["artifact_disposition"] == "discarded"
        assert receipt["completed_at"] is not None


def test_restart_reconciles_durable_discard_intent(api: APIContext):
    state = _start_review_quest(api)
    review_id, manifest_hash = _pending_review(state)
    artifacts = api.client.get(f"/api/v2/quests/{state['id']}/artifacts").json()[
        "items"
    ]
    paths = [api.sandbox_root / state["workspace"] / item["path"] for item in artifacts]
    assert paths and all(path.exists() for path in paths)

    storage = api.app.state.runtime_storage
    storage.begin_artifact_review(
        state["id"],
        review_id=review_id,
        manifest_hash=manifest_hash,
        idempotency_key="interrupted-discard",
        decision="discard",
        note=None,
        expected_state_version=state["state_version"],
        event_type="ArtifactDiscardRequested",
        patch={"status": "discarding", "pending_artifact_review": None},
    )

    recovery_storage = V1Storage(api.database_path)
    sandbox = Sandbox(api.sandbox_root)
    tools = build_default_registry(sandbox)
    recovery = V1QuestService(
        storage=recovery_storage,
        agent=RuleBasedAgent(),
        sandbox=sandbox,
        tools=tools,
        gateway=ToolGateway(tools, recovery_storage),
        max_workers=1,
    )
    try:
        recovered = recovery.get_quest(state["id"])
        assert recovered["status"] == "failed"
        assert recovered["artifact_disposition"] == "discarded"
        assert all(not path.exists() for path in paths)
    finally:
        recovery.close()
        recovery_storage.close()
