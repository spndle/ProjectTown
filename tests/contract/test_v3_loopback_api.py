from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app import v3_loopback_service
from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.v3_loopback_records import make_binding, serialize_record
from backend.app.v3_loopback_service import LoopbackService, LoopbackServiceError
from tests.v3_loopback_support import OPERATION_ID, loopback_ready


def test_loopback_feature_off_has_no_routes(tmp_path):
    app = create_app(
        {
            "database_path": tmp_path / "app.db",
            "sandbox_root": tmp_path / "sandbox",
            "enable_v3_loopback_ui": False,
        }
    )
    with TestClient(app) as client:
        assert client.get("/api/v3/bindings").status_code == 404
        assert client.get("/v3").status_code == 404
        assert "/api/v3/bindings" not in client.get("/openapi.json").json()["paths"]


def test_loopback_rejects_non_loopback_host_before_session(tmp_path):
    work = tmp_path / "work"
    (work / "bindings").mkdir(parents=True)
    (work / "idempotency").mkdir()
    app = create_app(
        {
            "database_path": tmp_path / "app.db",
            "sandbox_root": tmp_path / "sandbox",
            "enable_v3_loopback_ui": True,
            "v3_work_root": work,
        },
        loopback_service=LoopbackService(work, allow_test_client=True),
    )
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        response = client.post(
            "/api/v3/session", headers={"Origin": "http://127.0.0.1:8000"}
        )
        assert response.status_code == 200
        assert "csrf" in response.json()
        assert response.cookies.get("projecttown_v3_session") is not None
        assert (
            client.post(
                "/api/v3/session", headers={"Origin": "http://evil.invalid"}
            ).json()["error"]["code"]
            == "ORIGIN_REJECTED"
        )


def test_loopback_static_headers_session_and_single_dispatch(tmp_path):
    value = loopback_ready(tmp_path)
    work = value["work"]
    app = create_app(
        {
            "database_path": tmp_path / "app.db",
            "sandbox_root": tmp_path / "sandbox",
            "enable_v3_loopback_ui": True,
            "v3_work_root": work,
        },
        loopback_service=LoopbackService(work, allow_test_client=True),
    )
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        static = client.get("/v3")
        assert static.status_code == 200
        assert static.headers["content-security-policy"].startswith(
            "default-src 'none'"
        )
        assert static.headers["cache-control"] == "no-store"
        assert client.options("/api/v3/bindings").status_code in {401, 405}
        session = client.post(
            "/api/v3/session", headers={"Origin": "http://127.0.0.1:8000"}
        )
        csrf = session.json()["csrf"]
        cookie = session.headers["set-cookie"].lower()
        assert (
            "httponly" in cookie
            and "samesite=strict" in cookie
            and "path=/api/v3" in cookie
        )
        assert "secure" not in cookie
        headers = {
            "X-ProjectTown-V3-CSRF": csrf,
            "X-ProjectTown-V3-Operation": OPERATION_ID,
            "Origin": "http://127.0.0.1:8000",
            "Idempotency-Key": "k" * 16,
        }
        inspected = client.get(
            "/api/v3/operation", headers={"X-ProjectTown-V3-Operation": OPERATION_ID}
        )
        confirmation = inspected.json()["confirmations"]["apply"]
        first = client.post(
            "/api/v3/operation/apply",
            headers=headers,
            json={"confirmation": confirmation},
        )
        assert first.status_code == 200
        assert first.json()["outcome"] == "completed"
        second = client.post(
            "/api/v3/operation/apply",
            headers=headers,
            json={"confirmation": confirmation},
        )
        assert second.json() == first.json()
        assert (work / "idempotency").glob("*.json")
        assert (
            client.get(
                "/api/v3/operation/check",
                headers={"X-ProjectTown-V3-Operation": OPERATION_ID},
            ).json()["state"]
            == "COMMITTED"
        )


def test_loopback_rejects_forwarding_missing_session_and_bad_json(tmp_path):
    value = loopback_ready(tmp_path)
    work = value["work"]
    app = create_app(
        {
            "database_path": tmp_path / "app.db",
            "sandbox_root": tmp_path / "sandbox",
            "enable_v3_loopback_ui": True,
            "v3_work_root": work,
        },
        loopback_service=LoopbackService(work, allow_test_client=True),
    )
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert client.get("/api/v3/bindings").status_code == 401
        assert (
            client.post(
                "/api/v3/session",
                headers={"Origin": "http://127.0.0.1:8000", "Forwarded": "for=bad"},
            ).json()["error"]["code"]
            == "FORWARDED_HEADERS_REJECTED"
        )


def _session(client: TestClient) -> tuple[str, dict[str, str]]:
    response = client.post(
        "/api/v3/session", headers={"Origin": "http://127.0.0.1:8000"}
    )
    assert response.status_code == 200
    csrf = response.json()["csrf"]
    return csrf, {
        "Origin": "http://127.0.0.1:8000",
        "X-ProjectTown-V3-CSRF": csrf,
        "X-ProjectTown-V3-Operation": OPERATION_ID,
        "Idempotency-Key": "x" * 16,
    }


def _enabled(tmp_path):
    value = loopback_ready(tmp_path)
    app = create_app(
        {
            "database_path": tmp_path / "app.db",
            "sandbox_root": tmp_path / "sandbox",
            "enable_v3_loopback_ui": True,
            "v3_work_root": value["work"],
        },
        loopback_service=LoopbackService(value["work"], allow_test_client=True),
    )
    return value, app


@pytest.mark.parametrize(
    "header",
    ["Forwarded", "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto"],
)
def test_loopback_rejects_every_proxy_header(tmp_path, header):
    _, app = _enabled(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        response = client.post(
            "/api/v3/session",
            headers={"Origin": "http://127.0.0.1:8000", header: "canary"},
        )
    assert response.status_code == 403
    assert response.json() == {"error": {"code": "FORWARDED_HEADERS_REJECTED"}}
    assert "canary" not in response.text


def test_loopback_request_contract_and_redaction(tmp_path):
    value, app = _enabled(tmp_path)
    canaries = [
        str(value["root"]),
        str(value["authorization_path"]),
        value["authorization"].nonce,
    ]
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        csrf, headers = _session(client)
        assert csrf not in client.get("/api/v3/bindings").text
        assert (
            client.options("/api/v3/bindings").headers.get(
                "access-control-allow-origin"
            )
            is None
        )
        assert (
            client.get(
                "/api/v3/bindings", headers={"Host": "localhost:8000"}
            ).status_code
            == 403
        )
        mutation = "/api/v3/operation/apply"
        confirmation = f"APPLY {OPERATION_ID}"
        cases = [
            (
                {k: v for k, v in headers.items() if "CSRF" not in k},
                {"confirmation": confirmation},
                403,
            ),
            (
                {**headers, "Origin": "http://evil.invalid"},
                {"confirmation": confirmation},
                403,
            ),
            (
                {k: v for k, v in headers.items() if k != "Idempotency-Key"},
                {"confirmation": confirmation},
                400,
            ),
            (
                {**headers, "Idempotency-Key": "short"},
                {"confirmation": confirmation},
                400,
            ),
            (
                {**headers, "X-ProjectTown-V3-Operation": "bad"},
                {"confirmation": confirmation},
                400,
            ),
            (headers, {"confirmation": "wrong"}, 400),
            (headers, {"confirmation": confirmation, "extra": True}, 400),
        ]
        for current_headers, payload, expected in cases:
            response = client.post(mutation, headers=current_headers, json=payload)
            assert response.status_code == expected
            assert set(response.json()) == {"error"}
            assert all(canary not in response.text for canary in canaries)
        duplicate = b'{"confirmation":"a","confirmation":"b"}'
        response = client.post(
            mutation,
            headers={**headers, "Content-Type": "application/json"},
            content=duplicate,
        )
        assert response.status_code == 400
        response = client.post(mutation, headers=headers, content=b"{}")
        assert response.status_code == 415
        response = client.post(
            mutation,
            headers={**headers, "Content-Type": "application/json"},
            content=b"{" + b'"x":"' + b"a" * (17 * 1024) + b'"}',
        )
        assert response.status_code == 413


def test_session_restart_and_expiry_are_fail_closed(tmp_path, monkeypatch):
    value = loopback_ready(tmp_path)
    first = LoopbackService(value["work"], allow_test_client=True)
    token, csrf = first.bootstrap()
    first.verify_session(token, csrf, mutation=True)
    with pytest.raises(LoopbackServiceError, match="SESSION_REQUIRED"):
        LoopbackService(value["work"]).verify_session(token)
    clock = iter([0.0, 10.0, 10.0 + 15 * 60 + 1])
    monkeypatch.setattr(v3_loopback_service.time, "monotonic", lambda: next(clock))
    token, _ = first.bootstrap()
    first.verify_session(token)
    with pytest.raises(LoopbackServiceError, match="SESSION_EXPIRED"):
        first.verify_session(token)


def test_bootstrap_reclaims_expired_sessions_before_capacity_check(
    tmp_path, monkeypatch
):
    value = loopback_ready(tmp_path)
    service = LoopbackService(value["work"], allow_test_client=True)
    monkeypatch.setattr(v3_loopback_service, "_MAX_SESSIONS", 1)
    clock = iter([0.0, 15 * 60 + 1])
    monkeypatch.setattr(v3_loopback_service.time, "monotonic", lambda: next(clock))

    expired_token, _ = service.bootstrap()
    replacement_token, _ = service.bootstrap()

    assert expired_token != replacement_token
    assert expired_token not in service._sessions
    assert set(service._sessions) == {replacement_token}


def test_bootstrap_capacity_and_token_collision_never_overwrite(tmp_path, monkeypatch):
    value = loopback_ready(tmp_path)
    service = LoopbackService(value["work"], allow_test_client=True)
    values = iter(
        [
            "token-first",
            "csrf-first",
            "token-first",
            "csrf-collision",
            "token-second",
            "csrf-second",
        ]
    )
    monkeypatch.setattr(
        v3_loopback_service.secrets, "token_urlsafe", lambda _size: next(values)
    )

    first_token, first_csrf = service.bootstrap()
    second_token, second_csrf = service.bootstrap()

    assert (first_token, first_csrf) == ("token-first", "csrf-first")
    assert (second_token, second_csrf) == ("token-second", "csrf-second")
    assert service._sessions[first_token][0] == first_csrf
    monkeypatch.setattr(v3_loopback_service, "_MAX_SESSIONS", 2)
    with pytest.raises(LoopbackServiceError, match="SESSION_CAPACITY_REACHED") as error:
        service.bootstrap()
    assert error.value.status_code == 503


def test_feature_configuration_is_native_exact_and_opt_in(tmp_path, monkeypatch):
    missing = tmp_path / "missing"
    assert Settings(v3_work_root=missing).enable_v3_loopback_ui is False
    with pytest.raises((OSError, ValueError)):
        Settings(enable_v3_loopback_ui=True, v3_work_root=missing)
    work = tmp_path / "work"
    (work / "bindings").mkdir(parents=True)
    (work / "idempotency").mkdir()
    with pytest.raises(ValueError, match="v3_origin"):
        Settings(
            enable_v3_loopback_ui=True,
            v3_work_root=work,
            v3_origin="http://localhost:8000",
        )
    monkeypatch.setenv("container", "docker")
    with pytest.raises(ValueError, match="native-only"):
        Settings(enable_v3_loopback_ui=True, v3_work_root=work)


def test_non_loopback_socket_peer_is_rejected(tmp_path):
    value = loopback_ready(tmp_path)
    app = create_app(
        {
            "database_path": tmp_path / "app.db",
            "sandbox_root": tmp_path / "sandbox",
            "enable_v3_loopback_ui": True,
            "v3_work_root": value["work"],
        },
        loopback_service=LoopbackService(value["work"]),
    )
    with TestClient(
        app,
        base_url="http://127.0.0.1:8000",
        client=("192.0.2.10", 40000),
    ) as client:
        response = client.get("/v3")
    assert response.status_code == 403
    assert response.json() == {"error": {"code": "LOOPBACK_CLIENT_REQUIRED"}}


def test_rehashed_binding_display_drift_is_rejected_before_inspection(tmp_path):
    value = loopback_ready(tmp_path)
    fields = value["binding"].model_dump(exclude={"binding_hash"})
    fields["target_display"] = "different-target.md"
    changed = make_binding(**fields)
    path = value["work"] / "bindings" / f"{OPERATION_ID}.json"
    path.write_bytes(serialize_record(changed))

    with pytest.raises(LoopbackServiceError, match="AUTHORIZATION_BINDING_MISMATCH"):
        LoopbackService(value["work"]).inspect(OPERATION_ID)
