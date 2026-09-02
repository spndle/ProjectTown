from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.main import create_app


def _app(tmp_path):
    return create_app(
        {
            "database_path": tmp_path / "projecttown.db",
            "sandbox_root": tmp_path / "sandbox",
        }
    )


def _quest(client: TestClient, goal: str = "Create a safe failure navigation") -> dict:
    response = client.post("/api/v2/quests", json={"goal": goal})
    assert response.status_code == 201
    return response.json()


def _snapshot(storage, quest_id: str) -> tuple:
    return (
        storage.get_quest(quest_id)["state_version"],
        storage._conn.execute(
            "SELECT COUNT(*) FROM v1_events WHERE quest_id=?", (quest_id,)
        ).fetchone()[0],
        storage._conn.execute(
            "SELECT COUNT(*) FROM v1_tool_actions WHERE quest_id=?", (quest_id,)
        ).fetchone()[0],
        storage._conn.execute(
            "SELECT COUNT(*) FROM v1_artifact_reviews WHERE quest_id=?", (quest_id,)
        ).fetchone()[0],
    )


def test_failure_navigation_is_safe_strict_and_read_only(tmp_path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        quest = _quest(client)
        storage = app.state.runtime_storage
        assert storage is not None
        canary = "RAW_SECRET_TOOL_ARGS_PROVIDER_RESPONSE"
        storage.append_event(
            quest["id"],
            "ToolFailed",
            {
                "status": "failed",
                "error": {
                    "code": "TOOL_FAILED",
                    "message": canary,
                    "tool_args": {"secret": canary},
                },
            },
            quest["state_version"],
        )
        before = _snapshot(storage, quest["id"])
        response = client.get(f"/api/v2/quests/{quest['id']}/failure")
        assert response.status_code == 200
        payload = response.json()
        assert payload["summary"] == {
            "category": "tool_execution",
            "code": "TOOL_FAILED",
            "message": "A tool operation did not complete.",
            "recoverable": True,
        }
        assert payload["navigation"]["event"]["type"] == "ToolFailed"
        assert canary not in response.text
        assert _snapshot(storage, quest["id"]) == before
        schema = client.get("/openapi.json").json()["paths"][
            "/api/v2/quests/{quest_id}/failure"
        ]["get"]
        assert schema["responses"]["200"]["content"]["application/json"]["schema"][
            "$ref"
        ].endswith("FailureNavigationResponse")


def test_failure_navigation_missing_context_and_waiting_artifact_review(
    tmp_path,
) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        ordinary = _quest(client, "Create a context-free Quest")
        no_context = client.get(f"/api/v2/quests/{ordinary['id']}/failure")
        assert no_context.status_code == 200
        assert no_context.json()["summary"]["code"] == "FAILURE_CONTEXT_UNAVAILABLE"
        storage = app.state.runtime_storage
        assert storage is not None
        review = _quest(client, "Create an artifact review Quest")
        storage.append_event(
            review["id"],
            "ArtifactReviewRequested",
            {
                "status": "waiting_user",
                "artifact_review_required": True,
                "pending_artifact_review": {"review_id": "review-1"},
            },
            review["state_version"],
        )
        response = client.get(f"/api/v2/quests/{review['id']}/failure")
        assert response.status_code == 200
        assert response.json()["summary"]["category"] == "artifact_review"
        assert response.json()["navigation"]["artifact_review"]["pending"] is True
        assert client.get("/api/v2/quests/not-found/failure").status_code == 404


def test_failure_navigation_maps_budget_and_unknown_effect_without_raw_details(
    tmp_path,
) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        storage = app.state.runtime_storage
        assert storage is not None
        for code, category in (
            ("BUDGET_EXHAUSTED", "budget_rate_limit"),
            ("UNKNOWN_EFFECT", "unknown_effect"),
        ):
            quest = _quest(client, f"Create a {code} navigation test")
            storage.append_event(
                quest["id"],
                "FailureContext",
                {
                    "status": "failed",
                    "error": {
                        "code": code,
                        "message": "untrusted detail",
                        "provider_body": "untrusted detail",
                    },
                },
                quest["state_version"],
            )
            response = client.get(f"/api/v2/quests/{quest['id']}/failure")
            assert response.status_code == 200
            assert response.json()["summary"]["category"] == category
            assert "untrusted detail" not in response.text


def test_failure_navigation_filters_malicious_navigation_ids(tmp_path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        quest = _quest(client, "Create a malicious navigation identifier test")
        storage = app.state.runtime_storage
        assert storage is not None
        canary = "LEAK ME / % _ SECRET"
        action = storage.prepare_action(
            canary,
            quest["id"],
            "one",
            "idem-safe",
            "list_directory",
            "a" * 64,
            {},
            quest["state_version"],
        )
        storage.fail_action(action["action_id"], {"code": "TOOL_FAILED"})
        storage.append_evidence(
            quest["id"], canary, {"id": canary, "criterion_id": "safe"}
        )
        storage.append_decision(quest["id"], canary, {"id": canary, "kind": "modify"})
        with storage._transaction():
            storage._conn.execute(
                """INSERT INTO v1_artifact_reviews(quest_id, review_id, manifest_hash,
                idempotency_key, decision, note, created_at, completed_at)
                VALUES (?, ?, ?, 'safe', 'retain', NULL, 'now', NULL)""",
                (quest["id"], canary, "b" * 64),
            )
        current = storage.require_quest(quest["id"])
        storage.append_event(
            quest["id"],
            "ToolFailed",
            {
                "status": "failed",
                "current_milestone_id": canary,
                "error": {"code": "TOOL_FAILED", "message": canary},
            },
            current["state_version"],
        )
        response = client.get(f"/api/v2/quests/{quest['id']}/failure")
        assert response.status_code == 200
        navigation = response.json()["navigation"]
        assert navigation["milestone_id"] is None
        assert navigation["decision_id"] is None
        assert navigation["evidence_ids"] == []
        assert navigation["receipt"] is None
        assert navigation["artifact_review"]["review_id"] is None
        assert canary not in response.text
