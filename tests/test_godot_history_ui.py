from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "sandbox/tmp/godot-4.7.1/engine/Godot_v4.7.1-stable_win64_console.exe"


def test_history_ui_offline_smoke() -> None:
    result = subprocess.run(
        [
            str(ENGINE),
            "--headless",
            "--path",
            str(ROOT / "godot"),
            "-s",
            "res://tests/history_ui_smoke.gd",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "HISTORY_UI_SMOKE_OK" in result.stdout
