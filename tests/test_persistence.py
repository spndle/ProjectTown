from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import create_app

from .conftest import wait_for_terminal_quest


def test_sqlite_state_survives_application_restart(tmp_path: Path) -> None:
    config = {
        "database_path": tmp_path / "persistent.db",
        "sandbox_root": tmp_path / "sandbox",
        "max_workers": 1,
    }

    first_app = create_app(config)
    with TestClient(first_app) as first_client:
        create_response = first_client.post(
            "/api/v1/quests",
            json={
                "template_id": "readme_builder",
                "goal": "Persist a completed Quest and its execution history",
                "workspace": "quests/persistent",
            },
        )
        assert create_response.status_code == 201
        quest_id = create_response.json()["id"]
        assert first_client.post(f"/api/v1/quests/{quest_id}/run").status_code == 202
        completed_before_restart = wait_for_terminal_quest(first_client, quest_id)
        traces_before_restart = first_client.get(
            f"/api/v1/quests/{quest_id}/traces"
        ).json()["items"]

    assert Path(config["database_path"]).is_file()

    second_app = create_app(config)
    with TestClient(second_app) as second_client:
        quest_response = second_client.get(f"/api/v1/quests/{quest_id}")
        assert quest_response.status_code == 200
        completed_after_restart = quest_response.json()
        assert completed_after_restart == completed_before_restart

        list_payload = second_client.get("/api/v1/quests").json()
        assert list_payload["total"] == 1
        assert list_payload["items"] == [completed_before_restart]

        traces_after_restart = second_client.get(
            f"/api/v1/quests/{quest_id}/traces"
        ).json()["items"]
        assert traces_after_restart == traces_before_restart
        assert traces_after_restart[-1]["trace_type"] == "quest_completed"
