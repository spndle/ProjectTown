from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "sandbox/tmp/godot-4.7.1/engine/Godot_v4.7.1-stable_win64_console.exe"


def _run(script: str, marker: str) -> None:
    result = subprocess.run(
        [
            str(ENGINE),
            "--headless",
            "--path",
            str(ROOT / "godot"),
            "-s",
            f"res://tests/{script}",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker in result.stdout
    assert "test-only-key" not in result.stdout + result.stderr


def test_settings_ui_offline_smoke() -> None:
    _run("settings_ui_smoke.gd", "SETTINGS_UI_SMOKE_OK")


def test_settings_api_client_offline_smoke() -> None:
    _run("settings_api_client_smoke.gd", "SETTINGS_API_CLIENT_SMOKE_OK")


def test_settings_client_has_local_only_secret_boundaries() -> None:
    client = (ROOT / "godot/scripts/api_client.gd").read_text(encoding="utf-8")
    main = (ROOT / "godot/scripts/main.gd").read_text(encoding="utf-8")
    assert 'SETTINGS_ROUTE := "/local/settings/v1/providers/openai"' in client
    assert '"qwen": "/local/settings/v1/providers/qwen"' in client
    assert (
        'SETTINGS_TOKEN_PATH := "res://../.secrets/projecttown-settings-session.token"'
        in client
    )
    assert "X-ProjectTown-Settings-Token" in client
    assert "func _settings_backend_is_local() -> bool:" in client
    assert '"^http://(127\\\\.0\\\\.0\\\\.1|localhost):([1-9][0-9]{0,4})$"' in client
    fetch = client.split("func fetch_provider_settings", 1)[1].split(
        "func save_openai_settings", 1
    )[0]
    assert fetch.index("_settings_backend_is_local") < fetch.index("_settings_token")
    assert "ConfigFile" not in client + main
    assert "user://" not in client + main
    assert "api.openai.com" not in client
    assert "settings_api_key.secret = true" in main
    assert "settings_api_key.clear()" in main
    assert "func _close_settings_dialog() -> void:" in main
    assert "settings_dialog.close_requested.connect(_close_settings_dialog)" in main
    assert '"expected_revision": settings_revision' in main
    assert 'var settings_revision := ""' in main
    assert 'int(data.get("revision"' not in main
    assert 'expected_revision"] is String' in client
    assert "func _valid_settings_provider(provider: String) -> bool:" in client
    assert "func _on_settings_provider_selected(index: int) -> void:" in main
    assert '"openai", "qwen"' in main
    assert "var settings_base_url: LineEdit" in main
    assert "var settings_model: LineEdit" in main
    assert "settings_base_url.text.strip_edges()" in main
    assert "settings_model.text.strip_edges()" in main
    assert "其他值会被本地服务拒绝" in main
    assert "本地设置服务或会话令牌不可用" in main
