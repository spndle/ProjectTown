from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

import pytest

from backend.app.errors import ToolError
from backend.app.tools import Sandbox, build_default_registry
from backend.app.v1 import mcp_adapter
from backend.app.v1.mcp_adapter import install_local_mcp_adapter

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
CANARY_OUTPUT = {
    "type": "object",
    "properties": {"canary": {"type": "string"}},
    "required": ["canary"],
    "additionalProperties": False,
}
EMPTY_OUTPUT = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}


def config(tmp_path, *bindings, fixture=FIXTURE, **overrides):
    raw = {
        "executable": sys.executable,
        "argv": [str(fixture)],
        "cwd": str(tmp_path),
        "env": {},
        "timeout_seconds": 0.2,
        "bindings": list(bindings),
    }
    raw.update(overrides)
    return {"fixture": raw}


def binding(remote="echo", risk="read_only", schema=SCHEMA):
    output = (
        CANARY_OUTPUT
        if remote == "env_probe"
        else VALUE_OUTPUT
        if remote in {"echo", "increment"}
        else EMPTY_OUTPUT
    )
    return {
        "remote_tool": remote,
        "expected_tool": {
            "name": remote,
            "description": f"fixture {remote}",
            "inputSchema": schema,
            "outputSchema": output,
            "annotations": {
                "readOnlyHint": remote not in {"increment", "increment_then_timeout"}
            },
        },
        "risk": risk,
    }


def test_explicit_binding_discovers_and_has_stable_name(tmp_path):
    registry = build_default_registry(Sandbox(tmp_path / "sandbox"))
    result = install_local_mcp_adapter(registry, config(tmp_path, binding()))
    assert len(result.allowlist) == 1
    name = next(iter(result.allowlist))
    assert name.startswith("mcpv1_fixture_echo_")
    assert name in result.read_only_tools
    response = registry.execute(name, "quest", {"value": "ok"})
    assert response["result"] == {"value": "ok"}
    assert str(tmp_path) not in repr(response)


def test_schema_drift_and_sensitive_environment_fail_closed(tmp_path, monkeypatch):
    registry = build_default_registry(Sandbox(tmp_path / "sandbox"))
    with pytest.raises(ValueError):
        install_local_mcp_adapter(
            registry, config(tmp_path, binding(schema={"type": "object"}))
        )
    monkeypatch.setenv("UNSAFE_CANARY", "do-not-leak")
    with pytest.raises(ValueError):
        install_local_mcp_adapter(
            registry, config(tmp_path, binding(), env={"API_KEY": "no"})
        )


def test_timeout_malformed_and_output_flood_fail_closed(tmp_path):
    registry = build_default_registry(Sandbox(tmp_path / "sandbox"))
    result = install_local_mcp_adapter(
        registry,
        config(tmp_path, binding("timeout"), binding("malformed"), binding("flood")),
    )
    for name in result.allowlist:
        with pytest.raises(ToolError) as error:
            registry.execute(name, "quest", {"value": "x"})
        assert "Local MCP" in str(error.value)


def _wait_dead(pid: int) -> bool:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if os.name == "nt":
            kernel32 = ctypes.windll.kernel32
            kernel32.OpenProcess.argtypes = [
                wintypes.DWORD,
                wintypes.BOOL,
                wintypes.DWORD,
            ]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.GetExitCodeProcess.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(wintypes.DWORD),
            ]
            kernel32.GetExitCodeProcess.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return True
            exit_code = wintypes.DWORD()
            try:
                if (
                    kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                    and exit_code.value != 259
                ):
                    return True
            finally:
                kernel32.CloseHandle(handle)
        else:
            try:
                os.kill(pid, 0)
            except OSError:
                return True
        time.sleep(0.05)
    return False


def test_binding_name_changes_for_script_config_and_risk_drift(tmp_path):
    script = tmp_path / "mcp_stdio_server.py"
    script.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    names = []
    for suffix, changes in (
        ("base", {}),
        ("config", {"env": {"SAFE_MODE": "1"}}),
        ("risk", {}),
    ):
        registry = build_default_registry(Sandbox(tmp_path / suffix))
        remote = binding(risk="mutable") if suffix == "risk" else binding()
        installed = install_local_mcp_adapter(
            registry,
            config(tmp_path, remote, fixture=script, **changes),
        )
        names.append(next(iter(installed.allowlist)))
    script.write_text(
        script.read_text(encoding="utf-8") + "\n# identity-drift\n",
        encoding="utf-8",
    )
    registry = build_default_registry(Sandbox(tmp_path / "script"))
    installed = install_local_mcp_adapter(
        registry,
        config(tmp_path, binding(), fixture=script),
    )
    names.append(next(iter(installed.allowlist)))
    assert len(set(names)) == 4
    with pytest.raises(ToolError, match="not registered"):
        registry.execute(names[0], "quest", {"value": "old"})


def test_partial_install_and_schema_drift_preserve_default_registry(tmp_path):
    registry = build_default_registry(Sandbox(tmp_path / "sandbox"))
    defaults = registry.names
    with pytest.raises(ValueError):
        install_local_mcp_adapter(
            registry, config(tmp_path, binding(schema={"type": "object"}))
        )
    with pytest.raises(ValueError):
        install_local_mcp_adapter(
            registry, config(tmp_path, binding(), binding("missing_tool"))
        )
    assert registry.names == defaults


def test_parent_env_is_not_passed_and_processes_cleanup(tmp_path, monkeypatch):
    canary = "parent-only-canary"
    monkeypatch.setenv("UNSAFE_CANARY", canary)
    launch, parent, child = (
        tmp_path / "launch.log",
        tmp_path / "parent.pid",
        tmp_path / "child.pid",
    )
    registry = build_default_registry(Sandbox(tmp_path / "sandbox"))
    result = install_local_mcp_adapter(
        registry,
        config(
            tmp_path,
            binding("env_probe"),
            binding("timeout"),
            binding("spawn_timeout"),
            env={
                "LAUNCH_LOG": str(launch),
                "PID_FILE": str(parent),
                "CHILD_PID_FILE": str(child),
            },
        ),
    )
    probe = next(item for item in result.allowlist if "_env_probe_" in item)
    timeout = next(
        item for item in result.allowlist if "_timeout_" in item and "spawn" not in item
    )
    spawn = next(item for item in result.allowlist if "_spawn_timeout_" in item)
    response = registry.execute(probe, "quest", {"value": "x"})
    assert response["result"] == {"canary": "absent"}
    with pytest.raises(ToolError):
        registry.execute(timeout, "quest", {"value": "x"})
    assert parent.exists() and _wait_dead(int(parent.read_text()))
    with pytest.raises(ToolError) as error:
        registry.execute(spawn, "quest", {"value": "x"})
    for value in (response, error.value, repr(error.value)):
        assert (
            canary not in repr(value)
            and str(tmp_path) not in repr(value)
            and str(FIXTURE) not in repr(value)
        )
    assert launch.exists() and parent.exists() and child.exists()
    assert _wait_dead(int(parent.read_text())) and _wait_dead(int(child.read_text()))


def test_concurrent_calls_do_not_cross_and_close_leaves_no_sessions(tmp_path):
    registry = build_default_registry(Sandbox(tmp_path / "sandbox"))
    result = install_local_mcp_adapter(registry, config(tmp_path, binding()))
    name = next(iter(result.allowlist))
    replies: list[dict] = []
    threads = [
        threading.Thread(
            target=lambda value=value: replies.append(
                registry.execute(name, "quest", {"value": value})
            )
        )
        for value in ("left", "right")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
    assert {reply["result"]["value"] for reply in replies} == {"left", "right"}
    result.close()
    assert result._sessions is not None and not result._sessions._sessions


def test_session_close_cleans_every_pipe_when_one_close_fails(monkeypatch):
    events: list[str] = []

    class Pipe:
        def __init__(self, name: str, *, fail_once: bool = False) -> None:
            self.name = name
            self.fail_once = fail_once

        def close(self) -> None:
            events.append(f"close:{self.name}")
            if self.fail_once:
                self.fail_once = False
                raise BrokenPipeError

    class Reader:
        def join(self, timeout: float) -> None:
            events.append(f"join:{timeout}")

    class Controller:
        def discard(self, session) -> None:
            events.append("discard")

    session = object.__new__(mcp_adapter._Session)
    session._close_lock = threading.Lock()
    session._closed = False
    session.closing = threading.Event()
    session.process = type(
        "Process",
        (),
        {
            "stdin": Pipe("stdin", fail_once=True),
            "stdout": Pipe("stdout"),
            "stderr": Pipe("stderr"),
        },
    )()
    session.readers = [Reader()]
    session.controller = Controller()
    monkeypatch.setattr(mcp_adapter, "_stop", lambda process: events.append("stop"))

    session.close()
    session.close()

    assert session.closing.is_set()
    assert events == [
        "close:stdin",
        "stop",
        "join:1",
        "close:stdin",
        "close:stdout",
        "close:stderr",
        "discard",
    ]
