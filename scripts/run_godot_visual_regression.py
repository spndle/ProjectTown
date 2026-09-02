"""Fail-closed Windows visual regression capture runner; it never accepts goldens."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.visual import harness

ENGINE_VERSION = "4.7.1.stable.official.a13da4feb"
CONSOLE_SHA256 = "35dab11e04ece16a2b93035e65204f4a944a3e00b020d43e54409193379d5eef"
GUI_SHA256 = "323f9c4cc5db674e98815cdd8e69da007d5efc779abedc8c0e42883b7fdea12a"
TIMEOUT_SECONDS = 90


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def confined(path: Path, sandbox: Path) -> Path:
    resolved, base = path.resolve(), sandbox.resolve()
    if base != resolved and base not in resolved.parents:
        raise ValueError("path escapes sandbox")
    return resolved


def engine_guard(engine: Path, cwd: Path = PROJECT_ROOT) -> None:
    if (
        not engine.is_absolute()
        or not engine.is_file()
        or sha256(engine).lower() != CONSOLE_SHA256
    ):
        raise ValueError("console engine guard failed")
    gui = engine.with_name(engine.name.replace("_console", ""))
    if not gui.is_file() or sha256(gui).lower() != GUI_SHA256:
        raise ValueError("GUI engine guard failed")
    result = subprocess.run(
        [str(engine), "--version"],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        check=False,
        cwd=cwd,
    )
    if result.returncode or result.stdout.strip() != ENGINE_VERSION:
        raise ValueError("engine version guard failed")


def run(
    command: Sequence[str], env: dict[str, str], log: Path, cwd: Path = PROJECT_ROOT
) -> str:
    try:
        result = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            env=env,
            check=False,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired as error:
        log.write_text(
            "TIMEOUT\n" + (error.stdout or "") + (error.stderr or ""), encoding="utf-8"
        )
        raise RuntimeError("subprocess timeout") from error
    output = result.stdout + result.stderr
    log.write_text(output, encoding="utf-8")
    if result.returncode:
        raise RuntimeError("subprocess failed")
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--godot", type=Path)
    parser.add_argument("--candidate-root", type=Path)
    parser.add_argument("--diff-root", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--logs-root", type=Path)
    args = parser.parse_args(argv)
    project, sandbox = PROJECT_ROOT, PROJECT_ROOT / "sandbox"
    if os.name != "nt":
        print(json.dumps({"ok": False, "error": "windows_only"}))
        return 2
    if os.environ.get("PROJECTTOWN_UPDATE_GOLDENS") or (
        os.environ.get("CI") and os.environ.get("PROJECTTOWN_UPDATE_GOLDENS")
    ):
        print(json.dumps({"ok": False, "error": "golden_update_forbidden"}))
        return 2
    engine_value = args.godot or (
        Path(os.environ["PROJECTTOWN_GODOT_EXECUTABLE"])
        if os.environ.get("PROJECTTOWN_GODOT_EXECUTABLE")
        else None
    )
    if engine_value is None:
        print(json.dumps({"ok": False, "error": "godot_required"}))
        return 2
    try:
        candidate = confined(
            args.candidate_root or sandbox / "visual-candidates" / "windows", sandbox
        )
        diff = confined(args.diff_root or sandbox / "visual-diffs" / "windows", sandbox)
        report = confined(args.report or sandbox / "visual-report.json", sandbox)
        logs = confined(args.logs_root or sandbox / "visual-logs", sandbox)
    except ValueError:
        print(json.dumps({"ok": False, "error": "ValueError"}))
        return 2
    candidate.mkdir(parents=True, exist_ok=True)
    diff.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    try:
        engine_guard(engine_value, project)
        base_env = os.environ.copy()
        base_env.pop("PROJECTTOWN_UPDATE_GOLDENS", None)
        for fixture in harness.FIXTURE_IDS:
            run(
                [
                    str(engine_value),
                    "--headless",
                    "--path",
                    "godot",
                    "--rendering-method",
                    "gl_compatibility",
                    "--check-only",
                    "-s",
                    f"res://tests/capture/{fixture}.gd",
                ],
                base_env,
                logs / f"parse-{fixture}.log",
                project,
            )
        for fixture in harness.FIXTURE_IDS:
            for width, height in harness.VIEWPORTS:
                name = f"{fixture}-{width}x{height}.png"
                output = confined(candidate / name, sandbox)
                temporary = confined(candidate / f".{name}.tmp.png", sandbox)
                if temporary.exists():
                    if temporary.is_symlink():
                        raise RuntimeError("candidate temporary path is a symlink")
                    temporary.unlink()
                env = base_env | {"PROJECTTOWN_VISUAL_OUTPUT": str(temporary)}
                text = run(
                    [
                        str(engine_value),
                        "--path",
                        "godot",
                        "--rendering-method",
                        "gl_compatibility",
                        "--resolution",
                        f"{width}x{height}",
                        "-s",
                        f"res://tests/capture/{fixture}.gd",
                    ],
                    env,
                    logs / f"capture-{fixture}-{width}x{height}.log",
                    project,
                )
                marker = f"PROJECTTOWN_CAPTURE_OK fixture={fixture} path={temporary} size={width}x{height} offline=true"
                if (
                    marker not in text
                    or not temporary.is_file()
                    or harness.inspect_png(temporary.read_bytes()) != (width, height)
                ):
                    raise RuntimeError("capture marker or PNG guard failed")
                temporary.replace(output)
        results = harness.verify(
            project / "godot/tests/goldens/manifest.json",
            project / "godot/tests/goldens/windows",
            candidate,
            diff,
            sandbox,
            project,
        )
        payload = {"ok": all(item["passed"] for item in results), "results": results}
        report.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        print(
            json.dumps(
                {"ok": payload["ok"], "captures": len(results), "report": str(report)}
            )
        )
        return int(not payload["ok"])
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        report.write_text(
            json.dumps({"ok": False, "error": type(error).__name__}), encoding="utf-8"
        )
        print(json.dumps({"ok": False, "error": type(error).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
