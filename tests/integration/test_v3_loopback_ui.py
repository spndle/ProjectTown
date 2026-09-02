from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.v3_loopback_service import LoopbackService
from tests.v3_loopback_support import loopback_ready


def test_loopback_static_surface_is_local_csp_safe_and_semantic(tmp_path):
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
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        index = client.get("/v3")
        css = client.get("/v3/app.css")
        javascript = client.get("/v3/app.js")
    for response in (index, css, javascript):
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "Content-Security-Policy" in response.headers
    assert "text/html" in index.headers["content-type"]
    assert "text/css" in css.headers["content-type"]
    assert "javascript" in javascript.headers["content-type"]
    html, js = index.text, javascript.text
    for fragment in ("<main>", "<h1>", "<fieldset>", 'aria-live="polite"'):
        assert fragment in html
    assert '<script type="module"' in html and "<style" not in html
    for unsafe in ("innerHTML", "eval(", "serviceWorker", "localStorage", "cdn"):
        assert unsafe not in js.lower() if unsafe == "cdn" else unsafe not in js
    assert 'addEventListener("click", startSession)' in js
    assert "function initialize() { enable(false); }" in js
    assert "/operation/apply" not in js.split("function initialize", 1)[1]


def test_loopback_ui_rejects_remote_origin_even_without_session(tmp_path):
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
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert (
            client.get("/v3", headers={"Origin": "http://evil.invalid"}).status_code
            == 403
        )
