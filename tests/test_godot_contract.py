from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GODOT_ROOT = PROJECT_ROOT / "godot"


def test_godot_project_has_a_resolvable_main_scene() -> None:
    project = (GODOT_ROOT / "project.godot").read_text(encoding="utf-8")
    assert 'run/main_scene="res://main.tscn"' in project
    assert (GODOT_ROOT / "main.tscn").is_file()


def test_main_scene_references_existing_scripts_and_backend() -> None:
    scene = (GODOT_ROOT / "main.tscn").read_text(encoding="utf-8")
    for relative_script in (
        "scripts/main.gd",
        "scripts/town_view.gd",
    ):
        assert f"res://{relative_script}" in scene
        assert (GODOT_ROOT / relative_script).is_file()
    assert 'server_base_url = "http://127.0.0.1:8000"' in scene


def test_api_client_matches_v1_runtime_routes() -> None:
    client = (GODOT_ROOT / "scripts/api_client.gd").read_text(encoding="utf-8")
    expected_routes = (
        '"/health"',
        '"/api/v2/templates"',
        '"/api/v2/quests"',
        '"/api/v2/quests/%s/confirm"',
        '"/api/v2/quests/%s/%s"',
        '"/api/v2/quests/%s/events?after_sequence=%d"',
        '"/api/v2/quests/%s/evidence"',
    )
    for route in expected_routes:
        assert route in client
    assert "/api/v1/" not in client


def test_history_transport_is_separate_from_legacy_quests_signal() -> None:
    client = (GODOT_ROOT / "scripts/api_client.gd").read_text(encoding="utf-8")
    assert "func fetch_quests() -> void:" in client
    assert (
        '"/api/v2/quests", HTTPClient.METHOD_GET, {}, Callable(self, "_on_quests")'
        in client
    )
    assert (
        "signal quests_received(success: bool, items: Array, message: String)" in client
    )
    assert "signal quest_history_loading(request_context: Dictionary)" in client
    assert (
        "signal quest_history_received(success: bool, items: Array, total: int, message: String, request_context: Dictionary)"
        in client
    )
    assert (
        "signal quest_history_error(message: String, request_context: Dictionary)"
        in client
    )
    assert "func fetch_quest_history(" in client
    assert "func _history_request(" in client
    assert "q.uri_encode()" in client
    assert "status.uri_encode()" in client
    assert 'query.append("status=%s" % status.uri_encode())' in client
    assert 'query.append("offset=%d" % normalized_offset)' in client
    assert 'query.append("limit=%d" % normalized_limit)' in client
    assert 'Callable(self, "_on_quest_history").bind(context)' in client
    assert (
        "quest_history_received.emit(success, items, total, message, request_context)"
        in client
    )
    assert "quest_history_error.emit(message, request_context)" in client


def test_failure_transport_is_get_only_and_quest_id_is_encoded() -> None:
    client = (GODOT_ROOT / "scripts/api_client.gd").read_text(encoding="utf-8")
    body = client.split("func fetch_failure(quest_id: String) -> void:", 1)[1].split(
        "\nfunc ", 1
    )[0]
    assert '"/api/v2/quests/%s/failure" % quest_id.uri_encode()' in body
    assert "HTTPClient.METHOD_GET" in body
    assert "METHOD_POST" not in body
    assert (
        "signal quest_failure_received(success: bool, data: Dictionary, message: String, source_quest_id: String)"
        in client
    )
    assert (
        "signal quest_failure_error(message: String, source_quest_id: String)" in client
    )
    callback = client.split("func _on_failure(", 1)[1].split("\nfunc ", 1)[0]
    assert (
        "quest_failure_received.emit(success, data, message, source_quest_id)"
        in callback
    )
    assert "quest_failure_error.emit(message, source_quest_id)" in callback


def test_town_view_maps_all_v1_runtime_states() -> None:
    town = (GODOT_ROOT / "scripts/town_view.gd").read_text(encoding="utf-8")
    draw_layer = (GODOT_ROOT / "scripts/town_draw_layer.gd").read_text(encoding="utf-8")
    for status in (
        "idle",
        "draft",
        "planned",
        "running",
        "verifying",
        "replanning",
        "waiting_user",
        "paused",
        "recovering",
        "completed",
        "budget_exhausted",
        "failed",
    ):
        assert f'"{status}"' in town or f'"{status}"' in draw_layer
