"""Deterministic stdio MCP fixture; it is never a production server."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

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
OUTPUTS = {
    "echo": VALUE_OUTPUT,
    "increment": VALUE_OUTPUT,
    "increment_then_timeout": EMPTY_OUTPUT,
    "timeout": EMPTY_OUTPUT,
    "malformed": EMPTY_OUTPUT,
    "flood": EMPTY_OUTPUT,
    "env_probe": CANARY_OUTPUT,
    "spawn_timeout": EMPTY_OUTPUT,
}


def descriptor(name):
    return {
        "name": name,
        "description": f"fixture {name}",
        "inputSchema": SCHEMA,
        "outputSchema": OUTPUTS[name],
        "annotations": {
            "readOnlyHint": name not in {"increment", "increment_then_timeout"}
        },
    }


def reply(identifier, result):
    print(
        json.dumps({"jsonrpc": "2.0", "id": identifier, "result": result}), flush=True
    )


def main() -> int:
    for variable in ("LAUNCH_LOG", "PID_FILE"):
        target = os.environ.get(variable)
        if target:
            path = Path(target)
            path.parent.mkdir(parents=True, exist_ok=True)
            if variable == "LAUNCH_LOG":
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(f"{os.getpid()}\n")
            else:
                path.write_text(str(os.getpid()), encoding="utf-8")
    initialized = False
    for raw in sys.stdin:
        request = json.loads(raw)
        method = request.get("method")
        identifier = request.get("id")
        if method == "notifications/initialized":
            initialized = True
            continue
        if identifier is None:
            continue
        if method == "initialize":
            reply(
                identifier,
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "serverInfo": {"name": "fixture", "version": "1"},
                },
            )
        elif method == "tools/list":
            if not initialized:
                return 7
            reply(
                identifier,
                {
                    "tools": [
                        descriptor(name)
                        for name in (
                            "echo",
                            "increment",
                            "increment_then_timeout",
                            "timeout",
                            "malformed",
                            "flood",
                            "env_probe",
                            "spawn_timeout",
                        )
                    ]
                },
            )
        elif method == "tools/call":
            name = request["params"]["name"]
            args = request["params"].get("arguments", {})
            if name == "timeout":
                time.sleep(10)
                reply(identifier, {"structuredContent": {"late": True}})
            elif name == "malformed":
                print("not-json", flush=True)
            elif name == "flood":
                print("x" * 70_000, flush=True)
            elif name == "increment":
                counter = Path(os.environ["COUNTER_FILE"])
                value = int(counter.read_text() if counter.exists() else "0") + 1
                counter.write_text(str(value))
                reply(identifier, {"structuredContent": {"value": str(value)}})
            elif name == "increment_then_timeout":
                counter = Path(os.environ["COUNTER_FILE"])
                value = int(counter.read_text() if counter.exists() else "0") + 1
                counter.write_text(str(value))
                time.sleep(10)
            elif name == "spawn_timeout":
                child = subprocess.Popen(
                    [sys.executable, "-c", "import time; time.sleep(30)"]
                )
                Path(os.environ["CHILD_PID_FILE"]).write_text(str(child.pid))
                time.sleep(10)
            elif name == "env_probe":
                reply(
                    identifier,
                    {
                        "structuredContent": {
                            "canary": os.environ.get("UNSAFE_CANARY", "absent")
                        }
                    },
                )
            else:
                reply(identifier, {"structuredContent": {"value": args.get("value")}})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
