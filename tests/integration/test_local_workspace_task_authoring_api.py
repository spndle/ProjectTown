from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.local_workspace_task_api import LocalWorkspaceTaskService
from backend.app.local_workspace_task_authoring import initialize_work_root
from backend.app.main import create_app
from tests.unit.test_local_workspace_task import _ready as _v1_ready
from tests.unit.test_local_workspace_task_authoring import _roots


def _app(tmp_path):
    material, work = _roots(tmp_path)
    app = create_app(
        {
            "database_path": tmp_path / "authoring.db",
            "sandbox_root": tmp_path / "sandbox",
            "enable_v1_runtime": False,
            "enable_local_workspace_task": True,
            "enable_local_workspace_task_create": True,
            "local_workspace_task_root": work,
            "local_workspace_task_material_root": material,
        },
        local_workspace_task_service=LocalWorkspaceTaskService(
            work, allow_test_client=True
        ),
    )
    return app, material, work


def _session(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/workspace/session", headers={"Origin": "http://127.0.0.1:8000"}
    )
    assert response.status_code == 200
    return {"X-ProjectTown-Workspace-CSRF": response.json()["csrf"]}


def _mutation(headers: dict[str, str], key: str) -> dict[str, str]:
    return {
        **headers,
        "Origin": "http://127.0.0.1:8000",
        "Content-Type": "application/json",
        "Idempotency-Key": key,
    }


def test_default_off_and_authoring_end_to_end_without_paths(tmp_path) -> None:
    off = create_app(
        {"database_path": tmp_path / "off.db", "sandbox_root": tmp_path / "off-sandbox"}
    )
    with TestClient(off, base_url="http://127.0.0.1:8000") as client:
        assert client.get("/api/workspace/authoring/catalog").status_code == 404

    app, material, work = _app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        headers = _session(client)
        catalog = client.get("/api/workspace/authoring/catalog", headers=headers)
        assert catalog.status_code == 200
        assert str(material) not in catalog.text and str(work) not in catalog.text
        items = catalog.json()["items"]
        assert {item["relative_path"] for item in items} == {
            "nested/facts.txt",
            "notes.md",
        }
        assert all(
            "\\" not in item["relative_path"]
            and ":" not in item["relative_path"]
            and item["display_name"]
            for item in items
        )
        payload = {
            "task": "Create a local verification report.",
            "artifact_kind": "report",
            "source_ids": [item["source_id"] for item in items],
            "constraints": {"audience": "local reviewer"},
        }
        draft = client.post(
            "/api/workspace/authoring/drafts",
            headers=_mutation(headers, "draft-report"),
            json=payload,
        )
        assert draft.status_code == 200
        body = draft.json()
        assert body["state"] == "waiting_confirmation"
        assert "task" not in body and "constraints" not in body
        task_id = body["task_id"]
        wrong = client.post(
            f"/api/workspace/authoring/tasks/{task_id}/generate",
            headers=_mutation(headers, "generate-wrong"),
            json={
                "contract_hash": body["contract_hash"],
                "confirmation_phrase": "wrong",
            },
        )
        assert wrong.status_code == 400
        generated = client.post(
            f"/api/workspace/authoring/tasks/{task_id}/generate",
            headers=_mutation(headers, "generate-report"),
            json={
                "contract_hash": body["contract_hash"],
                "confirmation_phrase": body["confirmation_phrase"],
            },
        )
        assert generated.status_code == 200
        assert generated.json()["state"] == "generated"
        assert (
            client.get(
                f"/api/workspace/authoring/tasks/{task_id}/preview", headers=headers
            ).status_code
            == 200
        )
        for format_name in ("markdown", "pdf"):
            exported = client.post(
                f"/api/workspace/authoring/tasks/{task_id}/exports/{format_name}",
                headers=_mutation(headers, f"export-{format_name}"),
            )
            assert exported.status_code == 200
            download = client.get(
                f"/api/workspace/authoring/tasks/{task_id}/downloads/{format_name}",
                headers=headers,
            )
            assert download.status_code == 200 and download.content
        assert client.post(
            "/api/workspace/authoring/apply",
            headers=_mutation(headers, "nope"),
            json={},
        ).status_code in {404, 405}


def test_authoring_envelope_idempotency_and_extra_fields(tmp_path) -> None:
    app, _material, _work = _app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        headers = _session(client)
        catalog = client.get("/api/workspace/authoring/catalog", headers=headers).json()
        payload = {
            "task": "Create plan",
            "artifact_kind": "plan",
            "source_ids": [item["source_id"] for item in catalog["items"]],
        }
        assert (
            client.post(
                "/api/workspace/authoring/drafts", headers=headers, json=payload
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/workspace/authoring/drafts",
                headers={**_mutation(headers, "same"), "Forwarded": "for=bad"},
                json=payload,
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/workspace/authoring/drafts",
                headers={**_mutation(headers, "same"), "Host": "evil.invalid"},
                json=payload,
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/workspace/authoring/drafts",
                headers=_mutation(headers, "same"),
                json={**payload, "path": "C:/forbidden"},
            ).status_code
            == 422
        )
        oversized = client.post(
            "/api/workspace/authoring/drafts",
            headers={**_mutation(headers, "oversized"), "Content-Length": "65537"},
            content=b"{}",
        )
        assert oversized.status_code == 413
        first = client.post(
            "/api/workspace/authoring/drafts",
            headers=_mutation(headers, "same"),
            json=payload,
        )
        assert first.status_code == 200
        assert (
            client.post(
                "/api/workspace/authoring/drafts",
                headers=_mutation(headers, "same"),
                json=payload,
            ).json()
            == first.json()
        )
        conflict = client.post(
            "/api/workspace/authoring/drafts",
            headers=_mutation(headers, "same"),
            json={**payload, "task": "different"},
        )
        assert conflict.status_code == 409


def test_download_rejects_tamper_and_stale_material(tmp_path) -> None:
    app, material, work = _app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        headers = _session(client)
        catalog = client.get("/api/workspace/authoring/catalog", headers=headers).json()
        draft = client.post(
            "/api/workspace/authoring/drafts",
            headers=_mutation(headers, "tamper-draft"),
            json={
                "task": "Create a report",
                "artifact_kind": "report",
                "source_ids": [item["source_id"] for item in catalog["items"]],
            },
        ).json()
        task_id = draft["task_id"]
        assert (
            client.post(
                f"/api/workspace/authoring/tasks/{task_id}/generate",
                headers=_mutation(headers, "tamper-generate"),
                json={
                    "contract_hash": draft["contract_hash"],
                    "confirmation_phrase": draft["confirmation_phrase"],
                },
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/api/workspace/authoring/tasks/{task_id}/exports/markdown",
                headers=_mutation(headers, "tamper-export"),
            ).status_code
            == 200
        )
        export = work / "exports" / f"{task_id}.md"
        export.write_bytes(export.read_bytes() + b"tamper")
        assert (
            client.get(
                f"/api/workspace/authoring/tasks/{task_id}/downloads/markdown",
                headers=headers,
            ).status_code
            == 409
        )
        (material / "notes.md").write_text("changed source\n", encoding="utf-8")
        assert (
            client.get(
                f"/api/workspace/authoring/tasks/{task_id}/preview", headers=headers
            ).status_code
            == 409
        )


def test_create_flag_requires_read_only_feature_and_material_root(tmp_path) -> None:
    material, work = _roots(tmp_path)
    from pytest import raises

    with raises(ValueError, match="requires enable_local_workspace_task"):
        create_app(
            {
                "database_path": tmp_path / "bad.db",
                "sandbox_root": tmp_path / "bad-sandbox",
                "enable_local_workspace_task_create": True,
                "local_workspace_task_root": work,
                "local_workspace_task_material_root": material,
            }
        )


def test_authoring_state_rebuilds_after_application_restart(tmp_path) -> None:
    app, material, work = _app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        headers = _session(client)
        catalog = client.get("/api/workspace/authoring/catalog", headers=headers).json()
        draft = client.post(
            "/api/workspace/authoring/drafts",
            headers=_mutation(headers, "restart-draft"),
            json={
                "task": "Create a report",
                "artifact_kind": "report",
                "source_ids": [item["source_id"] for item in catalog["items"]],
            },
        ).json()
        assert (
            client.post(
                f"/api/workspace/authoring/tasks/{draft['task_id']}/generate",
                headers=_mutation(headers, "restart-generate"),
                json={
                    "contract_hash": draft["contract_hash"],
                    "confirmation_phrase": draft["confirmation_phrase"],
                },
            ).status_code
            == 200
        )
    restarted = create_app(
        {
            "database_path": tmp_path / "restart.db",
            "sandbox_root": tmp_path / "restart-sandbox",
            "enable_v1_runtime": False,
            "enable_local_workspace_task": True,
            "enable_local_workspace_task_create": True,
            "local_workspace_task_root": work,
            "local_workspace_task_material_root": material,
        },
        local_workspace_task_service=LocalWorkspaceTaskService(
            work, allow_test_client=True
        ),
    )
    with TestClient(restarted, base_url="http://127.0.0.1:8000") as client:
        headers = _session(client)
        response = client.get(
            f"/api/workspace/authoring/tasks/{draft['task_id']}/state", headers=headers
        )
        assert response.status_code == 200 and response.json()["state"] == "generated"


def test_v1_and_v2_bindings_are_isolated_in_one_work_root(tmp_path) -> None:
    v1 = _v1_ready(tmp_path)
    material, work = v1["material"], v1["work"]
    from backend.app.local_workspace_task import publish_binding

    publish_binding(work, v1["binding"])
    initialize_work_root(work, material)
    app = create_app(
        {
            "database_path": tmp_path / "mixed.db",
            "sandbox_root": tmp_path / "mixed-sandbox",
            "enable_v1_runtime": False,
            "enable_local_workspace_task": True,
            "enable_local_workspace_task_create": True,
            "local_workspace_task_root": work,
            "local_workspace_task_material_root": material,
        },
        local_workspace_task_service=LocalWorkspaceTaskService(
            work, allow_test_client=True
        ),
    )
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        headers = _session(client)
        catalog = client.get("/api/workspace/authoring/catalog", headers=headers).json()
        draft = client.post(
            "/api/workspace/authoring/drafts",
            headers=_mutation(headers, "mixed-draft"),
            json={
                "task": "Create a report",
                "artifact_kind": "report",
                "source_ids": [item["source_id"] for item in catalog["items"]],
            },
        ).json()
        task_id = draft["task_id"]
        assert (
            client.post(
                f"/api/workspace/authoring/tasks/{task_id}/generate",
                headers=_mutation(headers, "mixed-generate"),
                json={
                    "contract_hash": draft["contract_hash"],
                    "confirmation_phrase": draft["confirmation_phrase"],
                },
            ).status_code
            == 200
        )
        v1_listing = client.get("/api/workspace/tasks", headers=headers)
        assert v1_listing.status_code == 200
        assert v1_listing.json()["items"] == [
            {
                "task_id": "a" * 64,
                "task_label": "Summary",
                "artifact_kind": "report",
                "freshness": "verified",
            }
        ]
        assert (
            client.get(
                f"/api/workspace/authoring/tasks/{task_id}/state", headers=headers
            ).status_code
            == 200
        )
        assert (
            client.get(
                f"/api/workspace/authoring/tasks/{task_id}/preview", headers=headers
            ).status_code
            == 200
        )
        misplaced = work / "bindings" / f"{task_id}.json"
        misplaced.write_bytes(
            (work / "authoring-bindings" / f"{task_id}.json").read_bytes()
        )
        assert client.get("/api/workspace/tasks", headers=headers).status_code in {
            400,
            409,
        }
