extends SceneTree

## Deterministic offline regression for long Quest history entries in the
## right-side control panel. No backend transport is started.
const MAIN_SCENE := preload("res://main.tscn")
const WIDTH_CASES := [900.0, 1280.0, 1920.0]


func _initialize() -> void:
	call_deferred("_run")


func _run() -> void:
	var scene: Control = MAIN_SCENE.instantiate()
	scene.set("transport_enabled", false)
	root.add_child(scene)
	await process_frame
	await process_frame

	var quest_id := "qv1_9of3d7a1b2c4e5f6"
	var goal := "Create a fully executable Agent project brief with a deliberately long history label for right panel overflow regression coverage"
	scene.call("_on_quests_received", true, [{
		"id": quest_id,
		"status": "waiting_user",
		"goal": goal,
	}], "")
	await process_frame

	var history_select: OptionButton = scene.get("history_select") as OptionButton
	var restore_button: Button = scene.get("restore_quest_button") as Button
	var settings_button: Button = scene.get("settings_button") as Button
	var control_panel: PanelContainer = scene.get("control_panel") as PanelContainer
	var provenance_label: Label = scene.get("artifact_provenance_label") as Label
	if history_select == null or restore_button == null or settings_button == null or control_panel == null or provenance_label == null:
		_fail("right panel controls were not created")
		return
	provenance_label.text = "审计提示：收到未知 provenance 状态；请以成果预览和用户确认作为决定依据。"
	provenance_label.visible = true
	if provenance_label.autowrap_mode != TextServer.AUTOWRAP_WORD_SMART or provenance_label.size_flags_horizontal != Control.SIZE_EXPAND_FILL or provenance_label.custom_minimum_size.x > 0.0:
		_fail("provenance label is not horizontally expanding, wrapping, and width-unconstrained")
		return
	var expected_display_text := "[WAITING_USER] %s — %s" % [scene.call("_short_id", quest_id), goal.left(32)]
	var expected_tooltip := "[WAITING_USER] %s — %s" % [quest_id, goal]
	if history_select.fit_to_longest_item:
		_fail("history OptionButton still fits to its longest item")
		return
	if history_select.text_overrun_behavior != TextServer.OVERRUN_TRIM_ELLIPSIS or not history_select.clip_text:
		_fail("history OptionButton overflow handling is not ellipsis plus clipping")
		return
	if str(history_select.get_item_metadata(0)) != quest_id:
		_fail("history Quest metadata changed")
		return
	if history_select.get_item_text(0) != expected_display_text:
		_fail("history item display text changed")
		return
	if history_select.get_item_tooltip(0) != expected_tooltip:
		_fail("history item tooltip does not preserve the full label")
		return

	for width in WIDTH_CASES:
		scene.size = Vector2(width, 720.0)
		scene.call("_apply_responsive_layout")
		await process_frame
		await process_frame
		var panel_right := control_panel.get_global_rect().end.x
		var available_right := scene.get_global_rect().end.x
		if panel_right > available_right + 0.5:
			_fail("control panel exceeds scene at width %.0f: %.1f > %.1f" % [width, panel_right, available_right])
			return
		if history_select.get_global_rect().end.x > panel_right + 0.5:
			_fail("history selector exceeds control panel at width %.0f" % width)
			return
		if restore_button.get_global_rect().end.x > panel_right + 0.5:
			_fail("restore button exceeds control panel at width %.0f" % width)
			return
		if settings_button.get_global_rect().end.x > panel_right + 0.5:
			_fail("settings button exceeds control panel at width %.0f" % width)
			return
		if provenance_label.get_global_rect().end.x > panel_right + 0.5:
			_fail("provenance label exceeds control panel at width %.0f" % width)
			return

	print("RIGHT_PANEL_LAYOUT_SMOKE_OK widths=%s" % str(WIDTH_CASES))
	scene.queue_free()
	quit(0)


func _fail(message: String) -> void:
	printerr("RIGHT_PANEL_LAYOUT_SMOKE_FAILED: %s" % message)
	quit(1)
