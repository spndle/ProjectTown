from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.main import create_app


@dataclass(frozen=True)
class APIContext:
    client: TestClient
    app: FastAPI
    database_path: Path
    sandbox_root: Path


@pytest.fixture
def api(tmp_path: Path) -> Iterator[APIContext]:
    database_path = tmp_path / "projecttown.db"
    sandbox_root = tmp_path / "sandbox"
    app = create_app(
        {
            "database_path": database_path,
            "sandbox_root": sandbox_root,
            "max_workers": 2,
        }
    )
    with TestClient(app) as client:
        yield APIContext(
            client=client,
            app=app,
            database_path=database_path,
            sandbox_root=sandbox_root,
        )


def wait_for_terminal_quest(
    client: TestClient,
    quest_id: str,
    *,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Poll a Quest until it reaches a terminal state or fail with context."""

    deadline = time.monotonic() + timeout
    last_quest: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/quests/{quest_id}")
        assert response.status_code == 200, response.text
        last_quest = response.json()
        if last_quest["status"] in {"completed", "failed"}:
            return last_quest
        time.sleep(0.01)
    pytest.fail(
        f"Quest {quest_id!r} did not finish within {timeout:.1f}s; "
        f"last response: {last_quest!r}"
    )
