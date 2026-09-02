from __future__ import annotations

import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from backend.app.errors import ToolError
from backend.app.tools import Sandbox, build_default_registry

from .conftest import APIContext, wait_for_terminal_quest


@pytest.mark.parametrize(
    "workspace",
    (
        "../outside",
        "quests/../../outside",
        "C:/outside",
    ),
)
def test_quest_workspace_cannot_escape_sandbox(api: APIContext, workspace: str) -> None:
    response = api.client.post(
        "/api/v1/quests",
        json={
            "template_id": "project_brief",
            "goal": "This request must not create files outside the sandbox",
            "workspace": workspace,
        },
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "PATH_OUTSIDE_SANDBOX"
    assert error["request_id"] == response.headers["X-Request-ID"]


@pytest.mark.parametrize(
    "tool_name, arguments",
    (
        ("write_file", {"path": "../escape.md", "content": "blocked"}),
        ("write_file", {"path": "C:/escape.md", "content": "blocked"}),
        ("read_file", {"path": "../../outside.txt"}),
        ("list_directory", {"path": "../neighbor"}),
    ),
)
def test_tool_paths_cannot_escape_quest_workspace(
    tmp_path: Path,
    tool_name: str,
    arguments: dict[str, str],
) -> None:
    sandbox = Sandbox(tmp_path / "sandbox")
    registry = build_default_registry(sandbox)

    with pytest.raises(ToolError) as raised:
        registry.execute(tool_name, "quests/safe", arguments)

    assert raised.value.code == "PATH_OUTSIDE_WORKSPACE"
    assert not (tmp_path / "sandbox" / "quests" / "escape.md").exists()
    assert not (tmp_path / "escape.md").exists()


def test_duplicate_quest_start_returns_conflict(api: APIContext) -> None:
    entered_tool = threading.Event()
    release_tool = threading.Event()
    original_tool = api.app.state.tools._tools["list_directory"]

    def blocking_tool(workspace: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        entered_tool.set()
        if not release_tool.wait(timeout=5):
            raise RuntimeError("test did not release the blocking tool")
        return original_tool(workspace, arguments)

    api.app.state.tools._tools["list_directory"] = blocking_tool
    create_response = api.client.post(
        "/api/v1/quests",
        json={
            "template_id": "project_brief",
            "goal": "Keep this Quest running while duplicate-start behavior is tested",
            "workspace": "quests/duplicate-start",
        },
    )
    assert create_response.status_code == 201
    quest_id = create_response.json()["id"]

    try:
        first_response = api.client.post(f"/api/v1/quests/{quest_id}/run")
        assert first_response.status_code == 202
        assert entered_tool.wait(timeout=2), (
            "Quest worker did not enter the blocking tool"
        )

        duplicate_response = api.client.post(f"/api/v1/quests/{quest_id}/run")
        assert duplicate_response.status_code == 409
        error = duplicate_response.json()["error"]
        assert error["code"] == "QUEST_NOT_RUNNABLE"
        assert error["details"] == {"quest_id": quest_id, "status": "running"}
    finally:
        release_tool.set()

    completed = wait_for_terminal_quest(api.client, quest_id)
    assert completed["status"] == "completed"
