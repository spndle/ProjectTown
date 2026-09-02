from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.local_settings import (
    LOCAL_SETTINGS_PATH,
    QWEN_LOCAL_SETTINGS_PATH,
    SETTINGS_TOKEN_HEADER,
    LocalSettingsService,
)
from backend.app.main import create_app


def _config(tmp_path: Path, *, enabled: bool) -> dict[str, object]:
    return {
        "database_path": tmp_path / "app.db",
        "sandbox_root": tmp_path / "sandbox",
        "enable_v1_runtime": False,
        "enable_local_settings_control": enabled,
        "profile": "test",
        "secret_source": "local_file",
    }


def _container_config(
    tmp_path: Path, *, peer: str = "172.30.250.1"
) -> dict[str, object]:
    return {
        **_config(tmp_path, enabled=True),
        "allow_container_local_settings": True,
        "local_settings_trusted_peer": peer,
    }


def test_default_app_does_not_register_local_settings_route(tmp_path: Path) -> None:
    app = create_app(_config(tmp_path, enabled=False))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get(LOCAL_SETTINGS_PATH).status_code == 404
        assert client.get(QWEN_LOCAL_SETTINGS_PATH).status_code == 404


def test_enabled_control_plane_redacts_key_and_enforces_request_boundaries(
    tmp_path: Path,
) -> None:
    service = LocalSettingsService(root=tmp_path, allow_test_client=True)
    app = create_app(_config(tmp_path, enabled=True), local_settings_service=service)
    token_path = tmp_path / ".secrets" / "projecttown-settings-session.token"
    assert not token_path.exists()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert token_path.is_file()
        headers = {SETTINGS_TOKEN_HEADER: service.token}
        read = client.get(LOCAL_SETTINGS_PATH, headers=headers)
        assert read.status_code == 200 and read.json()["api_key_configured"] is False
        body = {
            "base_url": read.json()["base_url"],
            "model": read.json()["model"],
            "api_key_action": "replace",
            "api_key": "CANARY_API_KEY",
            "expected_revision": read.json()["revision"],
        }
        saved = client.put(
            LOCAL_SETTINGS_PATH,
            headers={**headers, "content-type": "application/json"},
            content=json.dumps(body),
        )
        assert saved.status_code == 200 and saved.json()["api_key_configured"] is True
        assert "CANARY_API_KEY" not in saved.text
        assert (
            client.get(
                LOCAL_SETTINGS_PATH, headers={**headers, "Origin": "http://evil"}
            ).status_code
            == 403
        )
        assert (
            client.get(LOCAL_SETTINGS_PATH + "?token=x", headers=headers).status_code
            == 403
        )
        assert (
            client.get(
                LOCAL_SETTINGS_PATH, headers={**headers, "Cookie": "token=x"}
            ).status_code
            == 403
        )
        assert (
            client.put(LOCAL_SETTINGS_PATH, headers=headers, content=b"{}").status_code
            == 415
        )
        duplicate = b'{"base_url":"https://api.openai.com/v1","base_url":"https://api.openai.com/v1"}'
        assert (
            client.put(
                LOCAL_SETTINGS_PATH,
                headers={**headers, "content-type": "application/json"},
                content=duplicate,
            ).status_code
            == 400
        )
        unknown = {**body, "unexpected": "x"}
        assert (
            client.put(
                LOCAL_SETTINGS_PATH,
                headers={**headers, "content-type": "application/json"},
                content=json.dumps(unknown),
            ).status_code
            == 400
        )
        replace_empty = {**body, "api_key": ""}
        assert (
            client.put(
                LOCAL_SETTINGS_PATH,
                headers={**headers, "content-type": "application/json"},
                content=json.dumps(replace_empty),
            ).status_code
            == 400
        )
    assert not token_path.exists()


def test_token_and_non_loopback_are_rejected(tmp_path: Path) -> None:
    service = LocalSettingsService(root=tmp_path, allow_test_client=True)
    app = create_app(_config(tmp_path, enabled=True), local_settings_service=service)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get(LOCAL_SETTINGS_PATH).status_code == 403
        assert (
            client.get(
                LOCAL_SETTINGS_PATH,
                headers={SETTINGS_TOKEN_HEADER: service.token, "host": "example.com"},
            ).status_code
            == 403
        )


@pytest.mark.parametrize(
    "host, accepted",
    [
        ("127.0.0.1", True),
        ("localhost:8000", True),
        ("[::1]:65535", True),
        ("127.0.0.1:evil", False),
        ("127.0.0.1:0", False),
        ("127.0.0.1:65536", False),
        ("user@127.0.0.1", False),
        ("127.0.0.1,evil", False),
        ("::1", False),
    ],
)
def test_host_parser_is_strict(tmp_path: Path, host: str, accepted: bool) -> None:
    service = LocalSettingsService(root=tmp_path, allow_test_client=True)
    app = create_app(_config(tmp_path, enabled=True), local_settings_service=service)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.get(
            LOCAL_SETTINGS_PATH,
            headers={SETTINGS_TOKEN_HEADER: service.token, "host": host},
        )
        assert (response.status_code == 200) is accepted


def test_filesystem_failure_is_stable_and_does_not_echo_canary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = LocalSettingsService(root=tmp_path, allow_test_client=True)
    app = create_app(_config(tmp_path, enabled=True), local_settings_service=service)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        headers = {SETTINGS_TOKEN_HEADER: service.token}
        current = client.get(LOCAL_SETTINGS_PATH, headers=headers).json()
        monkeypatch.setattr(
            service,
            "_atomic_write_settings",
            lambda *args: (_ for _ in ()).throw(OSError("CANARY_ACL_FAILURE")),
        )
        body = {
            "base_url": current["base_url"],
            "model": current["model"],
            "api_key_action": "clear",
            "api_key": None,
            "expected_revision": current["revision"],
        }
        response = client.put(
            LOCAL_SETTINGS_PATH,
            headers={**headers, "content-type": "application/json"},
            content=json.dumps(body),
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "LOCAL_SETTINGS_FILESYSTEM_ERROR"
        assert "CANARY_ACL_FAILURE" not in response.text


def test_qwen_route_is_redacted_preserves_openai_and_rejects_unknown_provider(
    tmp_path: Path,
) -> None:
    service = LocalSettingsService(root=tmp_path, allow_test_client=True)
    app = create_app(_config(tmp_path, enabled=True), local_settings_service=service)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        headers = {SETTINGS_TOKEN_HEADER: service.token}
        openai = client.get(LOCAL_SETTINGS_PATH, headers=headers).json()
        openai_body = {
            "base_url": openai["base_url"],
            "model": openai["model"],
            "api_key_action": "replace",
            "api_key": "OPENAI_API_CANARY",
            "expected_revision": openai["revision"],
        }
        assert (
            client.put(
                LOCAL_SETTINGS_PATH,
                headers={**headers, "content-type": "application/json"},
                content=json.dumps(openai_body),
            ).status_code
            == 200
        )
        qwen = client.get(QWEN_LOCAL_SETTINGS_PATH, headers=headers)
        assert qwen.status_code == 200
        assert qwen.json()["provider"] == "qwen"
        assert qwen.json()["base_url_options"] == []
        assert qwen.json()["live_authorized"] is False
        qwen_body = {
            "base_url": "https://workspace-example.cn-beijing.maas.aliyuncs.com/api/v1",
            "model": "qwen-plus",
            "api_key_action": "replace",
            "api_key": "QWEN_API_CANARY",
            "expected_revision": qwen.json()["revision"],
        }
        saved = client.put(
            QWEN_LOCAL_SETTINGS_PATH,
            headers={**headers, "content-type": "application/json"},
            content=json.dumps(qwen_body),
        )
        assert saved.status_code == 200 and saved.json()["api_key_configured"] is True
        assert (
            "QWEN_API_CANARY" not in saved.text
            and "OPENAI_API_CANARY" not in saved.text
        )
        openai_after = client.get(LOCAL_SETTINGS_PATH, headers=headers)
        assert (
            openai_after.status_code == 200
            and openai_after.json()["api_key_configured"] is True
        )
        assert (
            client.get(
                "/local/settings/v1/providers/deepseek", headers=headers
            ).status_code
            == 404
        )


def test_simulated_container_does_not_register_qwen_settings_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("container", "docker")
    app = create_app(_config(tmp_path, enabled=True))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get(QWEN_LOCAL_SETTINGS_PATH).status_code == 404


@pytest.mark.parametrize(
    "missing",
    [
        "enable_local_settings_control",
        "allow_container_local_settings",
        "profile",
        "secret_source",
        "local_settings_trusted_peer",
    ],
)
def test_container_route_requires_every_explicit_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
) -> None:
    monkeypatch.setattr(
        "backend.app.local_settings._is_container_environment", lambda: True
    )
    config = _container_config(tmp_path)
    if (
        missing == "enable_local_settings_control"
        or missing == "allow_container_local_settings"
    ):
        config[missing] = False
    elif missing == "profile":
        config[missing] = "production"
    elif missing == "secret_source":
        config[missing] = "environment"
    else:
        config[missing] = ""
        with pytest.raises(ValueError):
            create_app(config)
        return
    app = create_app(config)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get(LOCAL_SETTINGS_PATH).status_code == 404


@pytest.mark.parametrize(
    "peer", ["172.30.250.0/29", "localhost", "172.30.250.01", "::1"]
)
def test_container_trusted_peer_must_be_one_canonical_ipv4(
    tmp_path: Path, peer: str
) -> None:
    with pytest.raises(ValueError):
        create_app(_container_config(tmp_path, peer=peer))


def test_container_route_accepts_only_configured_gateway_peer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.app.local_settings._is_container_environment", lambda: True
    )
    service = LocalSettingsService(
        root=tmp_path,
        allow_test_client=False,
    )
    app = create_app(_container_config(tmp_path), local_settings_service=service)
    with TestClient(
        app, base_url="http://127.0.0.1", client=("172.30.250.1", 43111)
    ) as client:
        service._container_mode = (
            True  # Request-guard seam; Linux lock is tested in Docker.
        )
        service._trusted_peer = "172.30.250.1"
        headers = {SETTINGS_TOKEN_HEADER: service.token}
        assert client.get(LOCAL_SETTINGS_PATH, headers=headers).status_code == 200
        assert (
            client.get(
                LOCAL_SETTINGS_PATH, headers={**headers, "X-Forwarded-For": "127.0.0.1"}
            ).status_code
            == 200
        )
        assert (
            client.get(
                LOCAL_SETTINGS_PATH, headers={**headers, "Forwarded": "for=127.0.0.1"}
            ).status_code
            == 200
        )
        assert (
            client.get(
                LOCAL_SETTINGS_PATH, headers={**headers, "host": "example.com"}
            ).status_code
            == 403
        )
        assert (
            client.get(
                LOCAL_SETTINGS_PATH, headers={**headers, "Origin": "http://evil"}
            ).status_code
            == 403
        )
        assert (
            client.get(
                LOCAL_SETTINGS_PATH, headers={**headers, "Cookie": "x=y"}
            ).status_code
            == 403
        )
        assert (
            client.get(LOCAL_SETTINGS_PATH + "?x=y", headers=headers).status_code == 403
        )
        assert client.get(LOCAL_SETTINGS_PATH).status_code == 403
        client._transport.client = ("172.30.250.2", 43112)  # type: ignore[attr-defined]
        assert (
            client.get(
                LOCAL_SETTINGS_PATH, headers={SETTINGS_TOKEN_HEADER: service.token}
            ).status_code
            == 403
        )
