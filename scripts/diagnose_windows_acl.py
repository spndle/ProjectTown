"""Run the Local Settings Windows ACL primitive with safe stage-only tracing."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app import local_settings

TRACE_PREFIX = "PROJECTTOWN_ACL_TRACE:"
DIAGNOSTIC_TIMEOUT_SECONDS = 15
POWERSHELL = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
CONTROL_COMMAND = "[Console]::Out.WriteLine('PROJECTTOWN_ACL_TRACE:COMPLETE');exit 0"
ALLOWED_MARKERS = frozenset(
    f"{TRACE_PREFIX}{marker}" for marker in local_settings.WINDOWS_ACL_TRACE_MARKERS
)


def last_marker(output: bytes) -> str | None:
    try:
        lines = output.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return None
    return next((line for line in reversed(lines) if line in ALLOWED_MARKERS), None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--control", action="store_true")
    mode.add_argument("--path", type=Path)
    mode.add_argument("--verify-path", type=Path)
    args = parser.parse_args(argv)
    environment: dict[str, str] = {
        "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
        "WINDIR": os.environ.get("WINDIR", r"C:\Windows"),
    }
    command = CONTROL_COMMAND
    if not args.control:
        path = args.path or args.verify_path
        assert path is not None
        environment["PROJECTTOWN_LOCAL_SETTINGS_PATH"] = str(path.resolve())
        command = (
            local_settings._windows_acl_restrict_script(True, trace=True)
            if args.path is not None
            else local_settings._windows_acl_verify_script(True, trace=True)
        )
    try:
        completed = subprocess.run(
            [
                str(POWERSHELL),
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                command,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=DIAGNOSTIC_TIMEOUT_SECONDS,
            check=False,
            env=environment,
        )
    except subprocess.TimeoutExpired as error:
        marker = last_marker(error.stdout if isinstance(error.stdout, bytes) else b"")
        if marker is not None:
            print(marker)
        return 1
    except (OSError, subprocess.SubprocessError):
        return 1
    marker = last_marker(completed.stdout)
    if marker is not None:
        print(marker)
    return int(completed.returncode != 0 or marker != f"{TRACE_PREFIX}COMPLETE")


if __name__ == "__main__":
    raise SystemExit(main())
