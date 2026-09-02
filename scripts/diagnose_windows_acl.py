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
    parser.add_argument("--path", type=Path, required=True)
    args = parser.parse_args(argv)
    environment = {
        "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
        "WINDIR": os.environ.get("WINDIR", r"C:\Windows"),
        "PROJECTTOWN_LOCAL_SETTINGS_PATH": str(args.path.resolve()),
    }
    try:
        completed = subprocess.run(
            [
                str(POWERSHELL),
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                local_settings._windows_acl_restrict_script(True, trace=True),
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
