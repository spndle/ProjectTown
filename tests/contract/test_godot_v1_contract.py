from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GODOT = ROOT / "godot/scripts"


def test_v2_goal_contract_transport_and_controls() -> None:
    client = (GODOT / "api_client.gd").read_text(encoding="utf-8")
    for route in (
        '"/api/v2/quests"',
        '"/api/v2/quests/%s/confirm"',
        '"/api/v2/quests/%s/%s"',
        '"/api/v2/quests/%s/events?after_sequence=%d"',
        '"/api/v2/quests/%s/evidence"',
        '"/api/v2/quests/%s/artifacts"',
        '"/api/v2/quests/%s/artifacts/%s/preview"',
        '"/api/v2/quests/%s/artifacts/review"',
    ):
        assert route in client
    assert "WebSocketPeer" in client
    assert "last_sequence" in client
    assert "ordered at-least-once" in client
    for signal in (
        "quest_received",
        "events_received",
        "evidence_received",
        "event_received",
    ):
        assert f"signal {signal}" in client
        assert "source_quest_id" in client
    assert 'Callable(self, "_on_quest").bind(quest_id)' in client
    assert 'Callable(self, "_on_events").bind(quest_id)' in client
    assert 'Callable(self, "_on_evidence").bind(quest_id)' in client
    assert 'Callable(self, "_on_confirm").bind(quest_id)' in client
    assert 'Callable(self, "_on_run").bind(quest_id)' in client
    assert 'Callable(self, "_on_control").bind(action, quest_id)' in client
    assert 'Callable(self, "_on_decision").bind(quest_id)' in client
    assert 'Callable(self, "_on_artifact_review").bind(quest_id)' in client
    assert "func reset_quest_cursor(quest_id: String)" in client
    assert "signal quests_received" in client
    assert "func fetch_quests()" in client
    assert 'Callable(self, "_on_quests")' in client


def test_history_contract_preserves_query_order_and_offline_smoke() -> None:
    client = (GODOT / "api_client.gd").read_text(encoding="utf-8")
    smoke = (ROOT / "godot/tests/history_api_client_smoke.gd").read_text(
        encoding="utf-8"
    )
    history = client.split("func fetch_quest_history(", 1)[1].split(
        "\nfunc fetch_failure", 1
    )[0]
    assert "HTTPClient.METHOD_GET" in history
    assert "METHOD_POST" not in history
    assert "request_context.duplicate(true)" in history
    assert 'context["statuses"] = history["statuses"]' in history
    assert "maxi(offset, 0)" in history
    assert "clampi(limit, 1, 100)" in history
    assert '"&".join(query)' in history
    assert "status.uri_encode()" in history
    assert "quest_history_loading.emit(context)" in history
    assert "HISTORY_API_CLIENT_SMOKE_OK" in smoke
    assert "中文 空格%_&" in smoke
    assert "status=failed&status=waiting%20user" in smoke
    assert "offset=0&limit=100" in smoke
    assert "HTTPClient" not in smoke


def test_main_confirms_before_run_and_tracks_runtime_state() -> None:
    main = (GODOT / "main.gd").read_text(encoding="utf-8")
    assert "api.confirm_quest(active_quest_id" in main
    assert 'run_quest", active_quest_id, state_version' in main
    assert "确认并运行" in main
    assert "_format_contract" in main
    assert "Goal Contract" in main
    assert "submit_decision" in main
    assert "Evidence" in main
    for field in (
        "state_version",
        "budget_usage",
        "events_received",
        "evidence_received",
    ):
        assert field in main
    assert "if source_quest_id != active_quest_id:" in main
    assert "func _activate_quest(quest_id: String)" in main
    assert "func _on_quests_received" in main
    assert "func _on_restore_quest_pressed" in main
    assert "api.fetch_quests()" in main
    assert "api.reset_quest_cursor(quest_id)" in main
    assert "state_version = 0" in main
    assert "api.fetch_artifacts(active_quest_id)" in main
    for handler in (
        "_on_quest_confirmed",
        "_on_quest_started",
        "_on_quest_controlled",
        "_on_decision_submitted",
        "_on_artifact_reviewed",
    ):
        body = main.split(f"func {handler}(", 1)[1].split("\nfunc ", 1)[0]
        assert "source_quest_id != active_quest_id" in body
    review_body = main.split("if is_artifact_review:", 1)[1].split(
        "func _quest_progress", 1
    )[0]
    assert "api.fetch_artifacts(active_quest_id)" in review_body
    assert "func _stop_automatic_updates()" in main
    assert "poll_timer.stop()" in main
    assert "websocket_retry_seconds = minf(websocket_retry_seconds * 2.0, 30.0)" in main
    assert "not api.is_websocket_connected()" in main
    assert '"artifact_review_required": artifact_review_required' in (
        GODOT / "api_client.gd"
    ).read_text(encoding="utf-8")
    assert "func _on_artifacts_received" in main
    assert "func _on_artifact_preview_received" in main
    assert "func _on_keep_result_pressed" in main
    assert "func _on_discard_result_pressed" in main
    assert "items.size() == last_evidence_count" not in main
    assert "预览成果并作出选择" in main


def test_town_maps_every_runtime_status() -> None:
    town = (GODOT / "town_view.gd").read_text(encoding="utf-8")
    draw_layer = (GODOT / "town_draw_layer.gd").read_text(encoding="utf-8")
    for status in (
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
    assert '"成果验收所"' in draw_layer
    assert '"预览与确认"' in draw_layer
    assert "const PIXEL" in draw_layer
    for asset in (
        "dawn-town-backdrop-v3.png",
        "noon-town-backdrop-v3.png",
        "sunset-town-backdrop-v2.png",
        "night-town-backdrop-v3.png",
        "cloud-far-v1.png",
        "cloud-mid-v1.png",
        "cloud-near-v1.png",
        "guild-buildings-atlas-outline-v4.png",
        "guild-courier-sheet-outline-v4.png",
        "town-props-atlas-outline-v4.png",
        "fusion-pixel-12px-proportional-zh-hans.ttf",
    ):
        assert asset in town or asset in draw_layer
    assert "CanvasItem.TEXTURE_FILTER_NEAREST" in town
    assert "draw_texture_rect_region" in town
    assert "ThemeDB.fallback_font" not in town
    assert "func _draw_building(" not in town
    assert "func _draw_agent(" not in town
    assert "Time.get_time_dict_from_system()" in town
    assert "func set_display_time_minutes(minutes: float)" in town
    assert "smoothstep(0.0, 1.0, ratio)" in town
    assert "func _draw_scrolling_clouds" in town
    assert "CLOUD_TRANSPARENT_EDGE_PX" in town
    assert (
        "var tile_step := scaled_size.x - CLOUD_TRANSPARENT_EDGE_PX * 2.0 * scale_factor"
        in town
    )
    assert "cloud-clusters-atlas-v2.png" not in town
    assert "func _fit_text" in draw_layer
    assert "var compact := size.x < 470.0" in draw_layer
    assert "CanvasItem.TEXTURE_FILTER_LINEAR" in town
    assert "MODE_SHELL" in draw_layer
    assert "The padded gutter is deliberately" in town
    assert "1px near-black outline" in draw_layer
    assert "BASE_EDGE_CLEANUP_SHADER" in town
    assert "is_magenta_fringe" in town
    assert "BUILDING_OPAQUE_BASELINE := 162.0" in draw_layer
    assert "TREE_OPAQUE_BASELINE := 126.0" in draw_layer
    assert "PROP_OPAQUE_BASELINE := 68.0" in draw_layer
    assert (
        "COURIER_OPAQUE_BASELINES := [125.0, 125.0, 125.0, 125.0, 106.0, 106.0, 106.0, 106.0]"
        in draw_layer
    )
    assert "GROUND_SOURCE := Rect2(42, 205, 261, 40)" in draw_layer
    assert "func _draw_pixel_panel" in draw_layer


def test_main_uses_one_square_cornered_pixel_theme() -> None:
    main = (GODOT / "main.gd").read_text(encoding="utf-8")
    assert "func _install_pixel_theme()" in main
    assert "func _apply_button_palette" in main
    assert 'pixel_theme.set_stylebox("panel", "PopupMenu"' in main
    assert 'pixel_theme.set_stylebox("scroll", "VScrollBar"' in main
    assert 'pixel_theme.set_stylebox("panel", "AcceptDialog"' in main
    assert 'pixel_theme.set_stylebox("panel", "ConfirmationDialog"' in main
    assert 'pixel_theme.set_stylebox("embedded_border", "Window"' in main
    assert "box.set_corner_radius_all(0)" in main
    assert "func _pixel_frame(" in main
    assert "box.shadow_size = 1" in main
    assert 'scroll.add_theme_constant_override("scrollbar_width", 12)' in main
    assert (
        'scroll_content_margin.add_theme_constant_override("margin_right", 14)' in main
    )
    assert "pixel_theme.default_font_size = 14" in main
    assert "pixel_theme.default_font = PIXEL_FONT" in main
    assert "fusion-pixel-12px-proportional-zh-hans.ttf" in main
    assert "guild-courier-portraits-v2.png" in main
    for control_type in (
        "Label",
        "Button",
        "LineEdit",
        "TextEdit",
        "OptionButton",
        "ItemList",
        "ProgressBar",
        "PopupMenu",
        "TooltipLabel",
    ):
        assert f'pixel_theme.set_font("font", "{control_type}", PIXEL_FONT)' in main
    assert 'pixel_theme.set_font("normal_font", "RichTextLabel", PIXEL_FONT)' in main
    assert "onboarding_portrait.texture_filter = Control.TEXTURE_FILTER_NEAREST" in main
    assert "func _apply_responsive_layout()" in main
    assert "resized.connect(_apply_responsive_layout)" in main
    assert "rounded_width == _applied_responsive_width" in main
    assert (
        "popup_centered(Vector2i(mini(560, available.x), mini(390, available.y)))"
        in main
    )


def test_town_split_and_artifact_detail_view_contract() -> None:
    scene = (ROOT / "godot/main.tscn").read_text(encoding="utf-8")
    main = (GODOT / "main.gd").read_text(encoding="utf-8")
    capture = (ROOT / "godot/tests/visual_capture_artifact_review.gd").read_text(
        encoding="utf-8"
    )
    assert "offset_right = 0.0" in scene
    assert "clip_contents = true" in scene
    assert "town_view.offset_right = 0.0" in main
    assert "layout_divider.offset_left = -2.0" in main
    for marker in (
        "active_preview_content",
        "active_preview_path",
        "active_preview_hash",
        "preview_ready",
        "artifact_preview_detail_button",
        "artifact_preview_dialog",
        "artifact_preview_dialog_text",
        "artifact_preview_dialog.visible = false",
        "func _clear_active_preview()",
        "func _show_artifact_preview_detail()",
        "func _on_result_preview_gui_input(event: InputEvent)",
        "mouse_event.double_click",
        "artifact_preview_dialog.close_requested.connect(artifact_preview_dialog.hide)",
        "artifact_preview_dialog.window_input.connect(_on_artifact_preview_dialog_input)",
        "artifact_preview_dialog.exclusive = true",
        "artifact_preview_dialog_text.editable = false",
        "artifact_preview_dialog_text.wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY",
        "artifact_preview_dialog_text.scroll_vertical = 0.0",
        "key_event.keycode == KEY_ESCAPE",
        "selected_hash == active_preview_hash",
        "artifact_preview_dialog.popup_centered(dialog_size)",
    ):
        assert marker in main
    assert (
        "api.fetch_artifact_preview"
        not in main.split("func _show_artifact_preview_detail()", 1)[1].split(
            "func _on_keep_result_pressed", 1
        )[0]
    )
    assert 'PROJECTTOWN_CAPTURE_MODE") == "detail"' in capture
    assert "@export var transport_enabled := true" in main
    assert (
        "if not transport_enabled:"
        in main.split("func _ready() -> void:", 1)[1].split(
            "func _build_interface()", 1
        )[0]
    )
    assert "_install_poll_timer(false)" in main
    assert "func _install_poll_timer(start: bool = true) -> void:" in main
    offline_setup = 'scene.set("transport_enabled", false)'
    assert offline_setup in capture
    assert capture.index(offline_setup) < capture.index("get_root().add_child(scene)")
    assert 'scene.get("poll_timer").stop()' in capture
    assert 'scene.get("api").disconnect_events()' not in capture
    assert 'scene.call("_show_artifact_preview_detail")' in capture


def test_legacy_v1_routes_are_not_used_by_client() -> None:
    client = (GODOT / "api_client.gd").read_text(encoding="utf-8")
    assert "/api/v1/" not in client


def test_real_engine_api_smoke_script_covers_rest_and_websocket() -> None:
    smoke = (ROOT / "godot/tests/api_smoke.gd").read_text(encoding="utf-8")
    for operation in (
        "fetch_health",
        "fetch_templates",
        "create_quest",
        "confirm_quest",
        "run_quest",
        "fetch_events",
        "fetch_evidence",
        "fetch_artifacts",
        "fetch_artifact_preview",
        "review_artifacts",
        "connect_events",
    ):
        assert operation in smoke
    assert "GODOT_API_SMOKE_OK" in smoke
    assert "events[3]" in smoke
    assert "evidence[3]" in smoke
    assert "websocket_event_source" in smoke


def test_restore_smoke_is_read_only_and_covers_review_projection() -> None:
    smoke = (ROOT / "godot/tests/restore_smoke.gd").read_text(encoding="utf-8")
    for operation in (
        "fetch_quests",
        "fetch_quest",
        "fetch_events",
        "fetch_evidence",
        "fetch_artifacts",
        "fetch_artifact_preview",
    ):
        assert operation in smoke
    assert "RESTORE_SMOKE_OK" in smoke
    assert "waiting_user" in smoke
    assert ".create_quest(" not in smoke
    assert ".confirm_quest(" not in smoke
    assert ".run_quest(" not in smoke
    assert ".review_artifacts(" not in smoke


def test_time_cycle_smoke_covers_anchors_and_smooth_midpoints() -> None:
    smoke = (ROOT / "godot/tests/time_cycle_smoke.gd").read_text(encoding="utf-8")
    assert "TIME_CYCLE_SMOKE_OK" in smoke
    assert "period_cases" in smoke
    assert 'town.call("_sky_blend")' in smoke
    assert 'town.call("_night_factor")' in smoke
