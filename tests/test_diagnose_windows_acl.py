from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from scripts import diagnose_windows_acl as diagnose


def test_script_entrypoint_can_import_backend_from_repository_root() -> None:
    completed = subprocess.run(
        [sys.executable, str(diagnose.__file__), "--help"],
        cwd=diagnose.REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "--path" in completed.stdout and "--control" in completed.stdout


def test_last_marker_reverse_scans_only_allowlisted_utf8_markers() -> None:
    assert diagnose.last_marker(b"noise\nPROJECTTOWN_ACL_TRACE:DACL_APPLIED\ntrailer") == (
        "PROJECTTOWN_ACL_TRACE:DACL_APPLIED"
    )
    assert diagnose.last_marker(b"PROJECTTOWN_ACL_TRACE:UNKNOWN\n") is None
    assert diagnose.last_marker(b"\xff") is None
    assert diagnose.last_marker(b"") is None


def test_main_prints_only_last_marker_and_uses_bounded_safe_runner(
    tmp_path: Path, capsys
) -> None:
    completed = subprocess.CompletedProcess(
        [], 0, b"untrusted output\nPROJECTTOWN_ACL_TRACE:COMPLETE\n", b"ignored"
    )
    with patch.object(diagnose.subprocess, "run", return_value=completed) as run:
        assert diagnose.main(["--path", str(tmp_path)]) == 0
    assert capsys.readouterr().out == "PROJECTTOWN_ACL_TRACE:COMPLETE\n"
    kwargs = run.call_args.kwargs
    assert kwargs["timeout"] == diagnose.DIAGNOSTIC_TIMEOUT_SECONDS
    assert kwargs["stderr"] is subprocess.DEVNULL
    assert kwargs["env"]["PROJECTTOWN_LOCAL_SETTINGS_PATH"] == str(tmp_path.resolve())
    assert run.call_args.args[0][:3] == [str(diagnose.POWERSHELL), "-NoProfile", "-NonInteractive"]


def test_control_uses_exact_minimal_environment_and_safe_command(capsys) -> None:
    completed = subprocess.CompletedProcess(
        [], 0, b"PROJECTTOWN_ACL_TRACE:COMPLETE\n", b""
    )
    with patch.object(diagnose.subprocess, "run", return_value=completed) as run:
        assert diagnose.main(["--control"]) == 0
    assert capsys.readouterr().out == "PROJECTTOWN_ACL_TRACE:COMPLETE\n"
    assert set(run.call_args.kwargs["env"]) == {"SystemRoot", "WINDIR"}
    assert run.call_args.args[0][-1] == diagnose.CONTROL_COMMAND


def test_control_fails_closed_without_printing_untrusted_output(capsys) -> None:
    outcomes: list[object] = [
        subprocess.TimeoutExpired(["powershell"], diagnose.DIAGNOSTIC_TIMEOUT_SECONDS),
        subprocess.CompletedProcess([], 1, b"not-a-marker\n", b""),
        subprocess.CompletedProcess([], 0, b"\xff", b""),
    ]
    for outcome in outcomes:
        runner = (
            patch.object(diagnose.subprocess, "run", side_effect=outcome)
            if isinstance(outcome, Exception)
            else patch.object(diagnose.subprocess, "run", return_value=outcome)
        )
        with runner:
            assert diagnose.main(["--control"]) == 1
        assert capsys.readouterr().out == ""


def test_control_timeout_with_safe_marker_still_fails_closed(capsys) -> None:
    timeout = subprocess.TimeoutExpired(
        ["powershell"],
        diagnose.DIAGNOSTIC_TIMEOUT_SECONDS,
        output=b"PROJECTTOWN_ACL_TRACE:COMPLETE\n",
    )
    with patch.object(diagnose.subprocess, "run", side_effect=timeout):
        assert diagnose.main(["--control"]) == 1
    assert capsys.readouterr().out == "PROJECTTOWN_ACL_TRACE:COMPLETE\n"


def test_main_prints_safe_marker_but_fails_for_nonzero_or_incomplete_stage(
    tmp_path: Path, capsys
) -> None:
    outcomes = [
        subprocess.CompletedProcess([], 1, b"PROJECTTOWN_ACL_TRACE:OWNER_VALIDATED\n", b""),
        subprocess.CompletedProcess([], 1, b"PROJECTTOWN_ACL_TRACE:COMPLETE\n", b""),
        subprocess.CompletedProcess([], 0, b"PROJECTTOWN_ACL_TRACE:DACL_APPLIED\n", b""),
    ]
    for outcome in outcomes:
        with patch.object(diagnose.subprocess, "run", return_value=outcome):
            assert diagnose.main(["--path", str(tmp_path)]) == 1
        expected = (
            "PROJECTTOWN_ACL_TRACE:COMPLETE\n"
            if outcome.stdout == b"PROJECTTOWN_ACL_TRACE:COMPLETE\n"
            else "PROJECTTOWN_ACL_TRACE:OWNER_VALIDATED\n"
            if outcome.returncode
            else "PROJECTTOWN_ACL_TRACE:DACL_APPLIED\n"
        )
        assert capsys.readouterr().out == expected


def test_main_timeout_prints_only_the_last_safe_marker(tmp_path: Path, capsys) -> None:
    timeout = subprocess.TimeoutExpired(
        ["powershell"],
        diagnose.DIAGNOSTIC_TIMEOUT_SECONDS,
        output=b"untrusted\nPROJECTTOWN_ACL_TRACE:DACL_PREPARED\ntrailer",
    )
    with patch.object(diagnose.subprocess, "run", side_effect=timeout):
        assert diagnose.main(["--path", str(tmp_path)]) == 1
    assert capsys.readouterr().out == "PROJECTTOWN_ACL_TRACE:DACL_PREPARED\n"


def test_main_timeout_or_failure_without_safe_marker_prints_nothing(
    tmp_path: Path, capsys
) -> None:
    outcomes: list[object] = [
        subprocess.TimeoutExpired(["powershell"], diagnose.DIAGNOSTIC_TIMEOUT_SECONDS),
        subprocess.TimeoutExpired(
            ["powershell"], diagnose.DIAGNOSTIC_TIMEOUT_SECONDS, output=b"\xff"
        ),
        subprocess.CompletedProcess([], 0, b"not-a-marker\n", b""),
    ]
    for outcome in outcomes:
        runner = (
            patch.object(diagnose.subprocess, "run", side_effect=outcome)
            if isinstance(outcome, Exception)
            else patch.object(diagnose.subprocess, "run", return_value=outcome)
        )
        with runner:
            assert diagnose.main(["--path", str(tmp_path)]) == 1
        assert capsys.readouterr().out == ""
