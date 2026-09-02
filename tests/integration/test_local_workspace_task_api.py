from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.local_workspace_task_api import LocalWorkspaceTaskService
from backend.app.main import create_app
from tests.unit.test_local_workspace_task import _ready


def test_default_off_and_enabled_read_only_projection(tmp_path):
    default = create_app(
        {"database_path": tmp_path / "default.db", "sandbox_root": tmp_path / "sandbox"}
    )
    with TestClient(default, base_url="http://127.0.0.1:8000") as client:
        assert client.get("/workspace").status_code == 404
    value = _ready(tmp_path)
    from backend.app.local_workspace_task import publish_binding

    publish_binding(value["work"], value["binding"])
    app = create_app(
        {
            "database_path": tmp_path / "enabled.db",
            "sandbox_root": tmp_path / "sandbox2",
            "enable_local_workspace_task": True,
            "local_workspace_task_root": value["work"],
        },
        local_workspace_task_service=LocalWorkspaceTaskService(
            value["work"], allow_test_client=True
        ),
    )
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        index = client.get("/workspace")
        assert index.status_code == 200
        assert "Candidate exports are create-only local copies" in index.text
        assert "Apply" not in index.text and "Publish" not in index.text
        session = client.post(
            "/api/workspace/session", headers={"Origin": "http://127.0.0.1:8000"}
        )
        csrf = session.json()["csrf"]
        assert client.get("/api/workspace/tasks").status_code == 403
        assert (
            client.get(
                "/api/workspace/tasks",
                headers={"X-ProjectTown-Workspace-CSRF": "wrong"},
            ).status_code
            == 403
        )
        listing = client.get(
            "/api/workspace/tasks", headers={"X-ProjectTown-Workspace-CSRF": csrf}
        )
        assert listing.status_code == 200
        task_id = listing.json()["items"][0]["task_id"]
        detail = client.get(
            f"/api/workspace/tasks/{task_id}",
            headers={"X-ProjectTown-Workspace-CSRF": csrf},
        )
        assert detail.status_code == 200
        assert (
            str(value["work"]) not in detail.text
            and str(value["material"]) not in detail.text
        )
        assert (
            client.post("/api/workspace/tasks", json={"path": "x"}).status_code == 405
        )
        assert (
            client.get(
                "/workspace", headers={"Origin": "http://evil.invalid"}
            ).status_code
            == 403
        )


def test_workspace_only_mode_rejects_non_loopback_origin(tmp_path):
    value = _ready(tmp_path)
    from pytest import raises

    with raises(ValueError, match="v3_origin"):
        create_app(
            {
                "database_path": tmp_path / "invalid-origin.db",
                "sandbox_root": tmp_path / "sandbox-origin",
                "enable_local_workspace_task": True,
                "local_workspace_task_root": value["work"],
                "v3_origin": "http://example.invalid:8000",
            }
        )
