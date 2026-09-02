from __future__ import annotations

import logging
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.telemetry import BoundedTelemetry, InMemoryTelemetrySink, NoOpTelemetry


def test_server_generates_unique_correlation_and_preserves_request_id(tmp_path) -> None:  # type: ignore[no-untyped-def]
    sink = InMemoryTelemetrySink()
    telemetry = BoundedTelemetry(sink, export_timeout_seconds=0.05)
    app = create_app(
        {
            "database_path": tmp_path / "projecttown.db",
            "sandbox_root": tmp_path / "sandbox",
        },
        telemetry=telemetry,
    )
    with TestClient(app) as client:
        first = client.get(
            "/health",
            headers={"X-Request-ID": "legacy-id", "X-Correlation-ID": "caller"},
        )
        second = client.get("/health", headers={"X-Request-ID": "legacy-id"})
    assert first.status_code == 200
    assert first.headers["X-Request-ID"] == "legacy-id"
    assert re.fullmatch(r"[0-9a-f]{32}", first.headers["X-Correlation-ID"])
    assert first.headers["X-Correlation-ID"] != "caller"
    assert second.headers["X-Correlation-ID"] != first.headers["X-Correlation-ID"]
    records = sink.snapshot()
    spans = [record for record in records if record.kind == "span"]
    metrics = [record for record in records if record.kind == "metric"]
    assert len(spans) == 2 and len(metrics) == 2
    assert all("correlation_id" in record.attributes for record in spans)
    assert all("correlation_id" not in record.attributes for record in metrics)
    assert {record.trace_id for record in spans} == {
        first.headers["X-Correlation-ID"],
        second.headers["X-Correlation-ID"],
    }
    assert all("legacy-id" not in repr(record) for record in records)


def test_default_error_log_excludes_exception_canary(caplog, tmp_path) -> None:  # type: ignore[no-untyped-def]
    app = create_app(
        {
            "database_path": tmp_path / "projecttown.db",
            "sandbox_root": tmp_path / "sandbox",
        }
    )

    @app.get("/explode")
    def explode() -> None:
        raise RuntimeError("CANARY_EXCEPTION_LOG_SECRET")

    with (
        caplog.at_level(logging.ERROR, logger="projecttown"),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        response = client.get("/explode")
    assert response.status_code == 500
    output = caplog.text
    assert "CANARY_EXCEPTION_LOG_SECRET" not in output
    assert "correlation_id=" in output


def test_debug_error_response_excludes_exception_message(tmp_path) -> None:  # type: ignore[no-untyped-def]
    app = create_app(
        {
            "database_path": tmp_path / "projecttown.db",
            "sandbox_root": tmp_path / "sandbox",
            "debug": True,
        }
    )

    @app.get("/debug-explode")
    def explode() -> None:
        raise RuntimeError("CANARY_DEBUG_RESPONSE_SECRET")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/debug-explode")
    assert response.status_code == 500
    assert "CANARY_DEBUG_RESPONSE_SECRET" not in response.text


class _CloseRaisesTelemetry(NoOpTelemetry):
    def close(self, timeout_seconds: float | None = None) -> None:
        del timeout_seconds
        raise RuntimeError("CANARY_CLOSE_FAILURE")


def test_lifespan_survives_telemetry_close_failure(tmp_path) -> None:  # type: ignore[no-untyped-def]
    app = create_app(
        {
            "database_path": tmp_path / "projecttown.db",
            "sandbox_root": tmp_path / "sandbox",
        },
        telemetry=_CloseRaisesTelemetry(),  # type: ignore[arg-type]
    )
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
    with pytest.raises(sqlite3.ProgrammingError):
        app.state.database._conn.execute("SELECT 1")
