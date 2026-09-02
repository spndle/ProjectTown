from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path

import pytest

from backend.app.errors import AppError, ToolError
from backend.app.main import create_app
from backend.app.v1.models import QuestCreate

FIXTURE = Path(__file__).parents[1] / "fixtures" / "mcp_stdio_server.py"
SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": "string"}},
    "required": ["value"],
    "additionalProperties": False,
}
VALUE_OUTPUT = {
    "type": "object",
    "properties": {"value": {"type": "string"}},
    "required": ["value"],
    "additionalProperties": False,
}
EMPTY_OUTPUT = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}


def server_config(tmp_path, extra_env=None):
    def binding(name, risk):
        output = VALUE_OUTPUT if name in {"echo", "increment"} else EMPTY_OUTPUT
        return {
            "remote_tool": name,
            "expected_tool": {
                "name": name,
                "description": f"fixture {name}",
                "inputSchema": SCHEMA,
                "outputSchema": output,
                "annotations": {
                    "readOnlyHint": name not in {"increment", "increment_then_timeout"}
                },
            },
            "risk": risk,
        }

    env = {"COUNTER_FILE": "counter.txt"}
    env.update(extra_env or {})
    return {
        "fixture": {
            "executable": sys.executable,
            "argv": [str(FIXTURE)],
            "cwd": str(tmp_path),
            "env": env,
            "timeout_seconds": 0.2,
            "bindings": [
                binding("echo", "read_only"),
                binding("increment", "mutable"),
                binding("increment_then_timeout", "mutable"),
                binding("timeout", "mutable"),
                binding("spawn_timeout", "mutable"),
            ],
        }
    }


def test_default_is_unchanged_and_enabled_requires_explicit_config(tmp_path):
    app = create_app(
        {"database_path": tmp_path / "a.sqlite", "sandbox_root": tmp_path / "sandbox"}
    )
    assert not any(name.startswith("mcpv1_") for name in app.state.tools.names)
    app.state.runtime_service.close(wait=True)
    app.state.runtime_storage.close()
    app.state.database.close()
    with pytest.raises(ValueError):
        create_app(
            {
                "database_path": tmp_path / "b.sqlite",
                "sandbox_root": tmp_path / "sandbox2",
                "enable_local_mcp": True,
            }
        )


def test_default_and_storage_startup_replay_do_not_launch_mcp(tmp_path):
    launch = tmp_path / "launch.log"
    config = {
        "database_path": tmp_path / "replay.sqlite",
        "sandbox_root": tmp_path / "sandbox",
    }
    first = create_app(
        config, local_mcp_servers=server_config(tmp_path, {"LAUNCH_LOG": str(launch)})
    )
    first.state.runtime_service.close(wait=True)
    first.state.runtime_storage.close()
    first.state.database.close()
    second = create_app(
        config, local_mcp_servers=server_config(tmp_path, {"LAUNCH_LOG": str(launch)})
    )
    second.state.runtime_service.close(wait=True)
    second.state.runtime_storage.close()
    second.state.database.close()
    assert not launch.exists()


def test_gateway_receipts_approval_unknown_effect_and_binding_drift(tmp_path):
    app = create_app(
        {
            "database_path": tmp_path / "a.sqlite",
            "sandbox_root": tmp_path / "sandbox",
            "enable_local_mcp": True,
        },
        local_mcp_servers=server_config(tmp_path),
    )
    names = app.state.tools.names
    echo = next(name for name in names if "_echo_" in name)
    increment = next(name for name in names if "_increment_" in name)
    timeout = next(name for name in names if "_increment_then_timeout_" in name)
    gateway = app.state.runtime_service.gateway
    quest = app.state.runtime_service.create_quest(
        QuestCreate(goal="MCP gateway test", workspace="w")
    )
    base = {
        "quest_id": quest["id"],
        "milestone_id": "m",
        "expected_state_version": quest["state_version"],
        "workspace": "w",
        "arguments": {"value": "ok"},
    }
    committed = gateway.execute(
        action_id="a", idempotency_key="a", tool_name=echo, **base
    )
    assert committed["status"] == "committed" and committed["result"]["result"] == {
        "value": "ok"
    }
    base["expected_state_version"] = app.state.runtime_service.get_quest(quest["id"])[
        "state_version"
    ]
    with pytest.raises(AppError):
        gateway.execute(action_id="b", idempotency_key="b", tool_name=increment, **base)
    changed = gateway.execute(
        action_id="b", idempotency_key="b", tool_name=increment, approved=True, **base
    )
    assert (
        changed["status"] == "committed"
        and (tmp_path / "counter.txt").read_text() == "1"
    )
    (tmp_path / "counter.txt").write_text("0")
    base["expected_state_version"] = app.state.runtime_service.get_quest(quest["id"])[
        "state_version"
    ]
    unknown = gateway.execute(
        action_id="c", idempotency_key="c", tool_name=timeout, approved=True, **base
    )
    assert unknown["status"] == "unknown_effect"
    assert (
        gateway.execute(
            action_id="c", idempotency_key="c", tool_name=timeout, approved=True, **base
        )["status"]
        == "unknown_effect"
    )
    assert (tmp_path / "counter.txt").read_text() == "1"
    assert not any(
        str(tmp_path) in repr(value) for value in (committed, changed, unknown)
    )
    app.state.runtime_service.close(wait=True)
    app.state.runtime_storage.close()
    app.state.database.close()


def test_restart_with_binding_drift_does_not_repeat_unknown_effect(tmp_path):
    database = tmp_path / "restart.sqlite"
    config = {
        "database_path": database,
        "sandbox_root": tmp_path / "sandbox",
        "enable_local_mcp": True,
    }
    first = create_app(config, local_mcp_servers=server_config(tmp_path))
    old_tool = next(
        name for name in first.state.tools.names if "_increment_then_timeout_" in name
    )
    quest = first.state.runtime_service.create_quest(
        QuestCreate(goal="MCP restart recovery test", workspace="w")
    )
    request = {
        "action_id": "restart-action",
        "quest_id": quest["id"],
        "milestone_id": "m",
        "idempotency_key": "restart-key",
        "expected_state_version": quest["state_version"],
        "workspace": "w",
        "tool_name": old_tool,
        "arguments": {"value": "once"},
        "approved": True,
    }
    receipt = first.state.runtime_service.gateway.execute(**request)
    assert receipt["status"] == "unknown_effect"
    assert (tmp_path / "counter.txt").read_text() == "1"
    first.state.runtime_service.close(wait=True)
    first.state.runtime_storage.close()
    first.state.database.close()

    second = create_app(
        config,
        local_mcp_servers=server_config(tmp_path, {"SAFE_MODE": "1"}),
    )
    assert old_tool not in second.state.tools.names
    restored = second.state.runtime_service.get_quest(quest["id"])
    action = second.state.runtime_storage.get_action("restart-action")
    assert action is not None and action["status"] == "unknown_effect"
    assert (tmp_path / "counter.txt").read_text() == "1"
    with pytest.raises(AppError) as rejected:
        second.state.runtime_service.gateway.execute(
            **{
                **request,
                "expected_state_version": restored["state_version"],
            }
        )
    assert rejected.value.code == "TOOL_NOT_ALLOWED"
    assert (tmp_path / "counter.txt").read_text() == "1"
    second.state.runtime_service.close(wait=True)
    second.state.runtime_storage.close()
    second.state.database.close()


def test_lifespan_cancels_active_mcp_session_before_runtime_close(tmp_path):
    app = create_app(
        {
            "database_path": tmp_path / "a.sqlite",
            "sandbox_root": tmp_path / "sandbox",
            "enable_local_mcp": True,
        },
        local_mcp_servers=server_config(tmp_path),
    )
    spawn = next(name for name in app.state.tools.names if "_spawn_timeout_" in name)
    failure: list[Exception] = []

    async def exercise() -> None:
        async with app.router.lifespan_context(app):
            worker = threading.Thread(target=lambda: _run_spawn(app, spawn, failure))
            worker.start()
            deadline = time.monotonic() + 2
            while (
                not app.state.mcp_install._sessions._sessions
                and time.monotonic() < deadline
            ):
                await asyncio.sleep(0.02)
        worker.join(timeout=3)
        assert not worker.is_alive()

    asyncio.run(exercise())
    assert failure and isinstance(failure[0], Exception)


def _run_spawn(app, name, failure):
    try:
        app.state.tools.execute(name, "quest", {"value": "x"})
    except ToolError as exc:  # expected cancellation boundary
        failure.append(exc)
