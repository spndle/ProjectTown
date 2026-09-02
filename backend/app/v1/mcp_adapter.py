"""Default-off, fixed-process local stdio MCP bindings for the v1 gateway."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import ToolError
from ..tools import ToolRegistry

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_SENSITIVE = re.compile(r"(?:key|token|secret|password|credential)", re.IGNORECASE)
_VERSION = "2025-06-18"
_MAX = 65_536
_DESC = {"name", "description", "inputSchema", "outputSchema", "annotations"}
_SCHEMA = {
    "type",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "enum",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
}


@dataclass(frozen=True, slots=True)
class Binding:
    remote_tool: str
    expected_tool: Mapping[str, Any]
    risk: str


@dataclass(frozen=True, slots=True)
class Server:
    server_id: str
    executable: Path
    argv: tuple[str, ...]
    cwd: Path
    env: Mapping[str, str]
    timeout: float
    stdout_limit: int
    stderr_limit: int
    bindings: tuple[Binding, ...]
    server_hash: str
    config_hash: str


@dataclass(slots=True)
class McpInstallResult:
    allowlist: frozenset[str]
    high_risk_tools: frozenset[str]
    read_only_tools: frozenset[str]
    _sessions: _SessionController | None = None

    def cancel_all(self) -> None:
        """Stop active local MCP process trees before runtime workers drain."""
        if self._sessions is not None:
            self._sessions.cancel_all()

    def close(self) -> None:
        self.cancel_all()


def install_local_mcp_adapter(
    registry: ToolRegistry, server_configs: Mapping[str, Any]
) -> McpInstallResult:
    if not isinstance(server_configs, Mapping) or not server_configs:
        raise ValueError("local MCP requires at least one explicit server config")
    pending: list[tuple[str, Server, Binding, str, str]] = []
    names: set[str] = set()
    for server in (_parse_server(k, v) for k, v in server_configs.items()):
        discovered = _list_tools(server)
        for binding in server.bindings:
            descriptor = discovered.get(binding.remote_tool)
            if descriptor is None or _hash(descriptor) != _hash(binding.expected_tool):
                raise ValueError("local MCP binding descriptor did not match discovery")
            schema_hash = _hash(descriptor)
            binding_hash = _hash(
                {
                    "server_hash": server.server_hash,
                    "config_hash": server.config_hash,
                    "schema_hash": schema_hash,
                    "risk": binding.risk,
                }
            )
            name = f"mcpv1_{server.server_id}_{binding.remote_tool}_{binding_hash[:12]}"
            if name in names or name in registry.names:
                raise ValueError(
                    "local MCP binding name conflicts with an existing tool"
                )
            names.add(name)
            pending.append((name, server, binding, schema_hash, binding_hash))
    sessions = _SessionController()
    for name, server, binding, schema_hash, binding_hash in pending:
        registry.register(
            name,
            f"Explicit local MCP binding for {server.server_id}/{binding.remote_tool}.",
            _bound(server, binding, schema_hash, binding_hash, sessions),
        )
    return McpInstallResult(
        frozenset(names),
        frozenset(n for n, _, b, _, _ in pending if b.risk == "mutable"),
        frozenset(n for n, _, b, _, _ in pending if b.risk == "read_only"),
        sessions,
    )


def _bound(
    server: Server,
    binding: Binding,
    schema_hash: str,
    binding_hash: str,
    sessions: _SessionController,
):
    def execute(_workspace: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        _validate(binding.expected_tool["inputSchema"], arguments)
        result = _call(server, binding.remote_tool, arguments, sessions)
        if "outputSchema" in binding.expected_tool:
            _validate(binding.expected_tool["outputSchema"], result)
        return {
            "mcp": {
                "server_id": server.server_id,
                "server_hash": server.server_hash,
                "config_hash": server.config_hash,
                "schema_hash": schema_hash,
                "binding_hash": binding_hash,
                "remote_tool": binding.remote_tool,
            },
            "result": result,
        }

    return execute


def _parse_server(server_id: object, raw: Any) -> Server:
    if not isinstance(server_id, str) or not _NAME.fullmatch(server_id):
        raise ValueError("local MCP server id is invalid")
    if not isinstance(raw, Mapping):
        raise TypeError("local MCP server config must be an object")
    allowed = {
        "executable",
        "argv",
        "cwd",
        "env",
        "timeout_seconds",
        "stdout_limit_bytes",
        "stderr_limit_bytes",
        "bindings",
    }
    if set(raw) - allowed:
        raise ValueError("local MCP server config has unsupported fields")
    executable = _file(raw.get("executable"), "executable")
    cwd = _directory(raw.get("cwd"), "cwd")
    argv_raw = raw.get("argv", ())
    if (
        not isinstance(argv_raw, Sequence)
        or isinstance(argv_raw, (str, bytes))
        or len(argv_raw) > 32
    ):
        raise ValueError("local MCP argv is invalid")
    argv = tuple(_string(x, "argv", 512) for x in argv_raw)
    argv_hashes: dict[str, str] = {}
    for index, argument in enumerate(argv):
        if Path(argument).is_absolute():
            argv_file = _file(Path(argument), "argv file")
            argv_hashes[str(index)] = _filehash(argv_file)
    env_raw = raw.get("env", {})
    if not isinstance(env_raw, Mapping) or len(env_raw) > 16:
        raise ValueError("local MCP env is invalid")
    env: dict[str, str] = {}
    for key, value in env_raw.items():
        if (
            not isinstance(key, str)
            or not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,63}", key)
            or _SENSITIVE.search(key)
        ):
            raise ValueError("local MCP env name is not permitted")
        env[key] = _string(value, "env value", 512)
    timeout = raw.get("timeout_seconds", 5.0)
    if (
        not isinstance(timeout, (int, float))
        or isinstance(timeout, bool)
        or not 0 < float(timeout) <= 30
    ):
        raise ValueError("local MCP timeout is invalid")
    stdout = _limit(raw.get("stdout_limit_bytes", _MAX))
    stderr = _limit(raw.get("stderr_limit_bytes", 4096))
    raw_bindings = raw.get("bindings")
    if (
        not isinstance(raw_bindings, Sequence)
        or isinstance(raw_bindings, (str, bytes))
        or not raw_bindings
    ):
        raise ValueError("local MCP bindings are required")
    bindings: list[Binding] = []
    remote_names: set[str] = set()
    for item in raw_bindings:
        if not isinstance(item, Mapping) or set(item) != {
            "remote_tool",
            "expected_tool",
            "risk",
        }:
            raise ValueError("local MCP binding is invalid")
        remote, descriptor, risk = (
            item["remote_tool"],
            item["expected_tool"],
            item["risk"],
        )
        if (
            not isinstance(remote, str)
            or not _NAME.fullmatch(remote)
            or remote in remote_names
            or not isinstance(descriptor, Mapping)
            or risk not in {"read_only", "mutable"}
        ):
            raise ValueError("local MCP binding is invalid")
        normalized = _descriptor(descriptor)
        if normalized["name"] != remote:
            raise ValueError("local MCP binding is invalid")
        remote_names.add(remote)
        bindings.append(Binding(remote, normalized, risk))
    identity = {
        "server_id": server_id,
        "executable": str(executable),
        "argv": argv,
        "argv_file_hashes": argv_hashes,
        "cwd": str(cwd),
        "env": dict(sorted(env.items())),
        "timeout": float(timeout),
        "stdout": stdout,
        "stderr": stderr,
    }
    return Server(
        server_id,
        executable,
        argv,
        cwd,
        env,
        float(timeout),
        stdout,
        stderr,
        tuple(bindings),
        _hash(
            {"executable_bytes": _filehash(executable), "argv_file_bytes": argv_hashes}
        ),
        _hash(identity),
    )


def _descriptor(value: Mapping[str, Any]) -> dict[str, Any]:
    if (
        set(value) - _DESC
        or not isinstance(value.get("name"), str)
        or not _NAME.fullmatch(value["name"])
        or not isinstance(value.get("inputSchema"), Mapping)
    ):
        raise ValueError("local MCP tool descriptor is invalid")
    result = {"name": value["name"], "inputSchema": _schema(value["inputSchema"])}
    if "description" in value:
        if not isinstance(value["description"], str):
            raise ValueError("local MCP tool descriptor is invalid")
        result["description"] = value["description"]
    if "annotations" in value:
        if not isinstance(value["annotations"], Mapping):
            raise ValueError("local MCP tool descriptor is invalid")
        result["annotations"] = _json(value["annotations"])
    if "outputSchema" in value:
        if not isinstance(value["outputSchema"], Mapping):
            raise ValueError("local MCP tool descriptor is invalid")
        result["outputSchema"] = _schema(value["outputSchema"])
    return result


def _schema(value: Mapping[str, Any]) -> dict[str, Any]:
    if (
        set(value) - _SCHEMA
        or not isinstance(value.get("type"), str)
        or value["type"]
        not in {"object", "array", "string", "integer", "number", "boolean"}
    ):
        raise ValueError("local MCP schema is unsupported")
    kind = value["type"]
    out: dict[str, Any] = {"type": kind}
    if "enum" in value:
        if not isinstance(value["enum"], list) or not value["enum"]:
            raise ValueError("local MCP schema is unsupported")
        out["enum"] = _json(value["enum"])
    if kind == "object":
        props, required, additional = (
            value.get("properties", {}),
            value.get("required", []),
            value.get("additionalProperties", False),
        )
        if (
            not isinstance(props, Mapping)
            or not isinstance(required, list)
            or not isinstance(additional, bool)
            or not all(isinstance(k, str) and k in props for k in required)
        ):
            raise ValueError("local MCP schema is unsupported")
        if not all(
            isinstance(k, str) and isinstance(v, Mapping) for k, v in props.items()
        ):
            raise ValueError("local MCP schema is unsupported")
        out.update(
            properties={k: _schema(v) for k, v in sorted(props.items())},
            required=sorted(set(required)),
            additionalProperties=additional,
        )
    if kind == "array":
        if not isinstance(value.get("items"), Mapping):
            raise ValueError("local MCP schema is unsupported")
        out["items"] = _schema(value["items"])
    for key in ("minLength", "maxLength", "minimum", "maximum"):
        if key in value:
            if not isinstance(value[key], (int, float)) or isinstance(value[key], bool):
                raise ValueError("local MCP schema is unsupported")
            out[key] = value[key]
    return out


def _validate(schema: Mapping[str, Any], value: Any) -> None:
    kind = schema["type"]
    valid = {
        "object": isinstance(value, Mapping),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
    }[kind]
    if not valid or ("enum" in schema and value not in schema["enum"]):
        raise ToolError(
            "INVALID_TOOL_ARGUMENTS",
            "Local MCP arguments did not match the bound schema",
        )
    if kind == "object":
        assert isinstance(value, Mapping)
        if (
            not schema["additionalProperties"]
            and any(k not in schema["properties"] for k in value)
        ) or any(k not in value for k in schema["required"]):
            raise ToolError(
                "INVALID_TOOL_ARGUMENTS",
                "Local MCP arguments did not match the bound schema",
            )
        for key, child in schema["properties"].items():
            if key in value:
                _validate(child, value[key])
    elif kind == "array":
        for item in value:
            _validate(schema["items"], item)
    elif (
        kind == "string"
        and (
            ("minLength" in schema and len(value) < schema["minLength"])
            or ("maxLength" in schema and len(value) > schema["maxLength"])
        )
        or kind in {"integer", "number"}
        and (
            ("minimum" in schema and value < schema["minimum"])
            or ("maximum" in schema and value > schema["maximum"])
        )
    ):
        raise ToolError(
            "INVALID_TOOL_ARGUMENTS",
            "Local MCP arguments did not match the bound schema",
        )


def _list_tools(server: Server) -> dict[str, Mapping[str, Any]]:
    result = _exchange(server, "tools/list", {}).get("result")
    tools = result.get("tools") if isinstance(result, Mapping) else None
    if not isinstance(tools, list):
        raise TypeError("local MCP discovery returned an invalid tool list")
    found: dict[str, Mapping[str, Any]] = {}
    for item in tools:
        if not isinstance(item, Mapping):
            raise TypeError("local MCP discovery returned an invalid tool")
        descriptor = _descriptor(item)
        if descriptor["name"] in found:
            raise ValueError("local MCP discovery returned duplicate tools")
        found[descriptor["name"]] = descriptor
    return found


def _call(
    server: Server,
    name: str,
    arguments: Mapping[str, Any],
    sessions: _SessionController,
) -> dict[str, Any]:
    response = _exchange(
        server, "tools/call", {"name": name, "arguments": dict(arguments)}, sessions
    )
    result = response.get("result")
    if (
        not isinstance(result, Mapping)
        or result.get("isError") is True
        or not isinstance(result.get("structuredContent"), Mapping)
    ):
        raise ToolError("MCP_CALL_FAILED", "Local MCP tool call failed")
    return _mapping(result["structuredContent"])


def _exchange(
    server: Server,
    method: str,
    params: Mapping[str, Any],
    sessions: _SessionController | None = None,
) -> Mapping[str, Any]:
    session = _Session(server, sessions)
    try:
        initial = session.request(
            1,
            "initialize",
            {
                "protocolVersion": _VERSION,
                "capabilities": {},
                "clientInfo": {"name": "projecttown", "version": "1"},
            },
        ).get("result")
        if (
            not isinstance(initial, Mapping)
            or initial.get("protocolVersion") != _VERSION
        ):
            raise ToolError(
                "MCP_PROTOCOL_ERROR",
                "Local MCP server did not negotiate the required protocol",
            )
        session.notify("notifications/initialized", {})
        return session.request(2, method, params)
    finally:
        session.close()


class _SessionController:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: set[_Session] = set()

    def add(self, session: _Session) -> None:
        with self._lock:
            self._sessions.add(session)

    def discard(self, session: _Session) -> None:
        with self._lock:
            self._sessions.discard(session)

    def cancel_all(self) -> None:
        with self._lock:
            sessions = tuple(self._sessions)
        for session in sessions:
            session.close()


class _Session:
    def __init__(
        self, server: Server, controller: _SessionController | None = None
    ) -> None:
        env = {"PYTHONIOENCODING": "utf-8"}
        if os.name == "nt":
            env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", r"C:\\Windows")
        env.update(server.env)
        flags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
        )
        try:
            self.process = subprocess.Popen(
                [str(server.executable), *server.argv],
                cwd=str(server.cwd),
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                start_new_session=os.name != "nt",
                creationflags=flags,
            )
        except OSError as error:
            raise ToolError(
                "MCP_UNAVAILABLE", "Local MCP server could not be started"
            ) from error
        self.server = server
        self.controller = controller
        self.events: queue.Queue[tuple[str, bytes | None]] = queue.Queue(maxsize=32)
        self.stdout = 0
        self.stderr = 0
        self.buffer = bytearray()
        self.closing = threading.Event()
        self._close_lock = threading.Lock()
        self._closed = False
        self.readers: list[threading.Thread] = []
        if controller is not None:
            controller.add(self)
        for stream, label in (
            (self.process.stdout, "stdout"),
            (self.process.stderr, "stderr"),
        ):
            assert stream is not None
            reader = threading.Thread(
                target=self._reader, args=(stream, label), daemon=True
            )
            self.readers.append(reader)
            reader.start()

    def _enqueue(self, event: tuple[str, bytes | None]) -> None:
        while not self.closing.is_set():
            try:
                self.events.put(event, timeout=0.05)
                return
            except queue.Full:
                continue

    def _reader(self, stream: Any, label: str) -> None:
        try:
            while not self.closing.is_set() and (chunk := stream.read1(4096)):
                self._enqueue((label, chunk))
        finally:
            self._enqueue((label, None))

    def _write(self, item: Mapping[str, Any]) -> None:
        try:
            assert self.process.stdin is not None
            self.process.stdin.write(
                (json.dumps(item, separators=(",", ":")) + "\n").encode()
            )
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise ToolError(
                "MCP_UNAVAILABLE", "Local MCP server did not accept the request"
            ) from error

    def notify(self, method: str, params: Mapping[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def request(
        self, identifier: int, method: str, params: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        self._write(
            {"jsonrpc": "2.0", "id": identifier, "method": method, "params": params}
        )
        deadline = time.monotonic() + self.server.timeout
        while True:
            try:
                stream, chunk = self.events.get(
                    timeout=max(0, deadline - time.monotonic())
                )
            except queue.Empty as error:
                raise ToolError(
                    "MCP_TIMEOUT", "Local MCP tool call timed out"
                ) from error
            if chunk is None:
                if stream == "stdout":
                    raise ToolError(
                        "MCP_PROTOCOL_ERROR",
                        "Local MCP server closed its response stream",
                    )
                continue
            if stream == "stderr":
                self.stderr += len(chunk)
                if self.stderr > self.server.stderr_limit:
                    raise ToolError(
                        "MCP_OUTPUT_LIMIT", "Local MCP response exceeded its limit"
                    )
                continue
            self.stdout += len(chunk)
            if self.stdout > self.server.stdout_limit:
                raise ToolError(
                    "MCP_OUTPUT_LIMIT", "Local MCP response exceeded its limit"
                )
            self.buffer.extend(chunk)
            while b"\n" in self.buffer:
                line, _, rest = self.buffer.partition(b"\n")
                self.buffer = bytearray(rest)
                try:
                    item = json.loads(line.decode())
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise ToolError(
                        "MCP_PROTOCOL_ERROR",
                        "Local MCP server returned invalid protocol data",
                    ) from error
                if (
                    not isinstance(item, Mapping)
                    or item.get("jsonrpc") != "2.0"
                    or item.get("id") != identifier
                ):
                    raise ToolError(
                        "MCP_PROTOCOL_ERROR",
                        "Local MCP server returned an unexpected response",
                    )
                return item

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self.closing.set()
            try:
                _close_pipe(self.process.stdin)
                _stop(self.process)
                for reader in self.readers:
                    reader.join(timeout=1)
            finally:
                for stream in (
                    self.process.stdin,
                    self.process.stdout,
                    self.process.stderr,
                ):
                    _close_pipe(stream)
                self._closed = True
                if self.controller is not None:
                    self.controller.discard(self)


def _close_pipe(stream: Any | None) -> None:
    if stream is None:
        return
    try:
        stream.close()
    except (BrokenPipeError, OSError, ValueError):
        pass


def _stop(process: subprocess.Popen[bytes] | None) -> None:
    if process is None:
        return
    if process.poll() is None:
        try:
            if os.name == "nt":
                taskkill = (
                    Path(os.environ.get("SYSTEMROOT", r"C:\\Windows"))
                    / "System32"
                    / "taskkill.exe"
                )
                subprocess.run(
                    [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                    check=False,
                    timeout=3,
                )
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except (OSError, subprocess.SubprocessError):
            try:
                process.kill()
            except OSError:
                pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def _file(value: object, field: str) -> Path:
    path = Path(value) if isinstance(value, (str, Path)) else None
    if (
        path is None
        or not path.is_absolute()
        or path.is_symlink()
        or not path.is_file()
    ):
        raise ValueError(f"local MCP {field} must be an absolute regular file")
    return path.resolve()


def _directory(value: object, field: str) -> Path:
    path = Path(value) if isinstance(value, str) else None
    if path is None or not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise ValueError(f"local MCP {field} must be an absolute directory")
    return path.resolve()


def _string(value: object, field: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or len(value) > maximum
    ):
        raise ValueError(f"local MCP {field} is invalid")
    return value


def _limit(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= _MAX:
        raise ValueError("local MCP output limit is invalid")
    return value


def _filehash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
    ).hexdigest()


def _json(value: Any) -> Any:
    return json.loads(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    )


def _mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    if len(data.encode()) > 16_384:
        raise ToolError("MCP_OUTPUT_LIMIT", "Local MCP tool result exceeded its limit")
    return json.loads(data)
