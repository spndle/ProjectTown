from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import run_godot_visual_regression as runner
from tests.visual import harness


def _project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, Path, Path, Path, Path]:
    project = tmp_path / "project"
    sandbox = project / "sandbox"
    candidate = sandbox / "round" / "candidate"
    diff = sandbox / "round" / "diff"
    report = sandbox / "round" / "report.json"
    logs = sandbox / "round" / "logs"
    (project / "godot" / "tests" / "goldens" / "windows").mkdir(parents=True)
    sandbox.mkdir()
    (project / "godot" / "project.godot").write_text("[application]", encoding="utf-8")
    (project / "godot" / "tests" / "goldens" / "manifest.json").write_text(
        "{}", encoding="utf-8"
    )
    monkeypatch.setattr(runner, "PROJECT_ROOT", project)
    return project, sandbox, candidate, diff, report, logs


def _main_args(
    project: Path, candidate: Path, diff: Path, report: Path, logs: Path
) -> list[str]:
    return [
        "--godot",
        "C:/Godot.exe",
        "--candidate-root",
        str(candidate),
        "--diff-root",
        str(diff),
        "--report",
        str(report),
        "--logs-root",
        str(logs),
    ]


def test_confined_rejects_escape(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    with pytest.raises(ValueError):
        runner.confined(sandbox / ".." / "outside", sandbox)


def test_engine_guard_rejects_bad_hash(tmp_path: Path) -> None:
    engine = tmp_path / "Godot_v4.7.1-stable_win64_console.exe"
    engine.write_bytes(b"bad")
    with pytest.raises(ValueError):
        runner.engine_guard(engine)


def test_run_timeout_writes_log(tmp_path: Path) -> None:
    expired = __import__("subprocess").TimeoutExpired(
        ["godot"], 1, output=b"out\xff", stderr=None
    )
    with (
        patch.object(runner.subprocess, "run", side_effect=expired),
        pytest.raises(RuntimeError),
    ):
        runner.run(["godot"], {}, tmp_path / "run.log")
    assert (tmp_path / "run.log").read_text(encoding="utf-8") == "TIMEOUT\nout\ufffd"


def test_run_uses_utf8_replaces_invalid_output_and_keeps_nonzero_failure(
    tmp_path: Path,
) -> None:
    result = __import__("subprocess").CompletedProcess([], 7, b"out\xff", None)
    with (
        patch.object(runner.subprocess, "run", return_value=result) as run,
        pytest.raises(RuntimeError, match="subprocess failed"),
    ):
        runner.run(["godot"], {}, tmp_path / "run.log")
    assert (tmp_path / "run.log").read_text(encoding="utf-8") == "out\ufffd"
    assert run.call_args.kwargs["encoding"] == "utf-8"
    assert run.call_args.kwargs["errors"] == "replace"


def test_capture_command_matrix_is_complete() -> None:
    assert len(runner.harness.FIXTURE_IDS) * len(runner.harness.VIEWPORTS) == 21


def test_main_rejects_update_before_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROJECTTOWN_UPDATE_GOLDENS", "1")
    with patch.object(runner, "engine_guard") as guard:
        assert runner.main(["--godot", "C:/absolute/godot.exe"]) == 2
    guard.assert_not_called()


def test_main_success_runs_each_parse_and_unique_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _, candidate, diff, report, logs = _project(tmp_path, monkeypatch)
    calls: list[tuple[list[str], dict[str, str], Path, Path]] = []

    def fake_run(command: list[str], env: dict[str, str], log: Path, cwd: Path) -> str:
        calls.append((command, env, log, cwd))
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("log", encoding="utf-8")
        if "--editor" in command:
            return "imported"
        if "--check-only" in command:
            return "parsed"
        width, height = map(int, command[command.index("--resolution") + 1].split("x"))
        fixture = Path(command[-1]).stem
        output = Path(env["PROJECTTOWN_VISUAL_OUTPUT"])
        output.write_bytes(
            harness.encode_png(
                harness.Image(width, height, bytes((1, 2, 3, 255)) * width * height)
            )
        )
        return f"PROJECTTOWN_CAPTURE_OK fixture={fixture} path={output} size={width}x{height} offline=true"

    results = [
        {"fixture": fixture, "viewport": [width, height], "passed": True}
        for fixture in harness.FIXTURE_IDS
        for width, height in harness.VIEWPORTS
    ]
    monkeypatch.setattr(runner, "engine_guard", lambda engine, cwd: None)
    monkeypatch.setattr(runner, "run", fake_run)
    monkeypatch.setattr(runner.harness, "verify", lambda *args: results)
    assert runner.main(_main_args(project, candidate, diff, report, logs)) == 0
    fixture_count = len(harness.FIXTURE_IDS)
    capture_count = fixture_count * len(harness.VIEWPORTS)
    imports = [call for call in calls if "--editor" in call[0]]
    assert len(imports) == 1
    assert Path(imports[0][0][0]) == Path("C:/Godot.exe")
    assert imports[0][0][1:] == ["--headless", "--editor", "--path", "godot", "--quit"]
    assert imports[0][2] == logs / "import.log"
    assert calls.index(imports[0]) == 0
    parses = [call for call in calls if "--check-only" in call[0]]
    assert len(parses) == fixture_count
    captures = [
        call for call in calls if "--check-only" not in call[0] and "--editor" not in call[0]
    ]
    assert len(captures) == capture_count and all(call[3] == project for call in calls)
    fullscreen = [call for call in captures if "--fullscreen" in call[0]]
    assert len(fullscreen) == fixture_count
    assert all(
        call[0][call[0].index("--resolution") + 1] == "1920x1080"
        for call in fullscreen
    )
    assert all(
        ("--fullscreen" in call[0])
        == (call[0][call[0].index("--resolution") + 1] == "1920x1080")
        for call in captures
    )
    outputs = [call[1]["PROJECTTOWN_VISUAL_OUTPUT"] for call in captures]
    assert len(set(outputs)) == capture_count and all(
        value.endswith(".tmp.png") for value in outputs
    )
    assert len(json.loads(report.read_text())["results"]) == capture_count
    assert len(list(candidate.glob("*.png"))) == capture_count


@pytest.mark.parametrize("stage", ["parse", "capture"])
def test_main_marker_failure_does_not_accept_old_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    project, _, candidate, diff, report, logs = _project(tmp_path, monkeypatch)
    old = candidate / "main-1280x720.png"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old")

    def fake_run(command: list[str], env: dict[str, str], log: Path, cwd: Path) -> str:
        if "--editor" in command:
            return "imported"
        if stage == "parse" and "--check-only" in command:
            raise RuntimeError("parse failure")
        if "--check-only" in command:
            return "ok"
        return "missing marker"

    monkeypatch.setattr(runner, "engine_guard", lambda engine, cwd: None)
    monkeypatch.setattr(runner, "run", fake_run)
    assert runner.main(_main_args(project, candidate, diff, report, logs)) == 2
    assert old.read_bytes() == b"old" and json.loads(report.read_text()) == {
        "ok": False,
        "error": "RuntimeError",
    }


def test_main_rejects_custom_sandbox_escape_before_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project, _, _, diff, report, logs = _project(tmp_path, monkeypatch)
    with patch.object(runner, "engine_guard") as guard:
        assert (
            runner.main(_main_args(project, tmp_path / "escape", diff, report, logs))
            == 2
        )
    guard.assert_not_called()
    assert capsys.readouterr().out == '{"ok": false, "error": "ValueError"}\n'


def test_engine_guard_checks_exact_console_gui_hashes_and_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = tmp_path / "Godot_v4.7.1-stable_win64_console.exe"
    gui = tmp_path / "Godot_v4.7.1-stable_win64.exe"
    engine.write_bytes(b"console")
    gui.write_bytes(b"gui")
    monkeypatch.setattr(
        runner,
        "sha256",
        lambda path: runner.CONSOLE_SHA256 if path == engine else runner.GUI_SHA256,
    )
    result = __import__("subprocess").CompletedProcess(
        [], 0, runner.ENGINE_VERSION + "\n", ""
    )
    with patch.object(runner.subprocess, "run", return_value=result):
        runner.engine_guard(engine, tmp_path)


def test_verify_failure_writes_controlled_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _, candidate, diff, report, logs = _project(tmp_path, monkeypatch)

    def fake_run(command: list[str], env: dict[str, str], log: Path, cwd: Path) -> str:
        if "--editor" in command:
            return "imported"
        if "--check-only" in command:
            return "ok"
        width, height = map(int, command[command.index("--resolution") + 1].split("x"))
        fixture = Path(command[-1]).stem
        output = Path(env["PROJECTTOWN_VISUAL_OUTPUT"])
        output.write_bytes(
            harness.encode_png(
                harness.Image(width, height, bytes((1, 2, 3, 255)) * width * height)
            )
        )
        return f"PROJECTTOWN_CAPTURE_OK fixture={fixture} path={output} size={width}x{height} offline=true"

    monkeypatch.setattr(runner, "engine_guard", lambda engine, cwd: None)
    monkeypatch.setattr(runner, "run", fake_run)
    monkeypatch.setattr(runner.harness, "verify", lambda *args: [{"passed": False}])
    assert runner.main(_main_args(project, candidate, diff, report, logs)) == 1
    assert json.loads(report.read_text())["ok"] is False


def test_workflow_pins_engine_and_never_sets_update_gate() -> None:
    workflow = Path(".github/workflows/visual-regression.yml").read_text(
        encoding="utf-8"
    )
    assert (
        "Godot_v4.7.1-stable_win64.exe.zip" in workflow
        and "C7A289051EAEFB460B0106B60E9CD5BEE0EF55FD102DCB2BED1EB356CF3D90A1"
        in workflow
    )
    assert "PROJECTTOWN_UPDATE_GOLDENS" not in workflow
    for step_name in (
        "Download pinned Godot archive",
        "Verify pinned Godot archive hash",
        "Extract pinned Godot archive",
        "Configure deterministic capture display",
        "Run Godot visual regression",
    ):
        assert workflow.count(f"- name: {step_name}") == 1
    assert "shell: powershell" in workflow
    assert "Set-DisplayResolution -Width 1920 -Height 1080 -Force" in workflow
    assert (
        '$display = ((Get-DisplayResolution | Out-String) -replace "\\x00", "").Trim()'
        in workflow
    )
    assert "$display -ne '1920x1080'" in workflow
    assert "capture display resolution mismatch" in workflow
    assert workflow.index("Configure deterministic capture display") < workflow.index(
        "Run Godot visual regression"
    )
    assert (
        "- run: pytest tests/test_visual_regression_runner.py tests/visual -q"
        in workflow
        and "- run: ruff check" in workflow
        and "if: always()" in workflow
    )
