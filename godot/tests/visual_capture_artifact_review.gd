extends SceneTree

var frames_remaining := 14


func _initialize() -> void:
	var packed_scene: PackedScene = load("res://main.tscn")
	var scene: Node = packed_scene.instantiate()
	# Disable transport before _ready() so this fixture never creates an API
	# client, schedules polling, or sends capture traffic to a real backend.
	scene.set("transport_enabled", false)
	get_root().add_child(scene)
	call_deferred("_apply_artifact_review_fixture", scene)


func _process(_delta: float) -> bool:
	frames_remaining -= 1
	if frames_remaining == 0:
		_capture()
	return false


func _apply_artifact_review_fixture(scene: Node) -> void:
	scene.set("active_quest_id", "qv1_visual_review")
	scene.set("active_status", "waiting_user")
	scene.set("result_review_key", "review_visual_01")
	scene.set("result_manifest_hash", "ca716d33")
	scene.set("artifact_review_pending", true)
	scene.set("active_artifact_id", "artifact_readme")
	scene.get("town_view").set_quest_state("waiting_user", 1.0, "为个人 Agent 项目创建可执行的项目简报")
	scene.get("onboarding_step_label").text = "第 4 步 · 预览成果并作出选择"
	scene.get("onboarding_help_label").text = "先阅读真实文件内容；满意就保留，不满意再安全丢弃。"
	scene.get("quest_id_label").text = "Quest · qv1_visual_review"
	scene.get("current_step_label").text = "请确认最终成果"
	scene.get("status_badge").text = "待确认"
	scene.get("progress_bar").value = 100.0
	scene.get("result_state_label").text = "验收已通过。请逐个查看成果，再选择保留或丢弃。"
	var result_select: OptionButton = scene.get("result_select")
	result_select.clear()
	result_select.add_item("项目简报.md")
	result_select.disabled = false
	var preview: TextEdit = scene.get("result_preview")
	preview.text = "# ProjectTown 项目简报\n\n目标：把个人目标变成可追踪、可验收的 Agent Quest。\n\n- 已生成里程碑计划\n- 已完成内容验收\n- 等待用户确认保留或丢弃"
	scene.set("active_preview_content", preview.text)
	scene.set("active_preview_path", "deliverables/project_brief.md")
	scene.set("active_preview_hash", "ca716d33")
	scene.set("preview_ready", true)
	# This is a fully local visual fixture. Stop transport before assigning the
	# synthetic Quest ID so captures never pollute a real backend with 404/403s.
	scene.get("poll_timer").stop()
	scene.get("artifact_preview_detail_button").disabled = false
	scene.get("keep_result_button").disabled = false
	scene.get("discard_result_button").disabled = false
	await process_frame
	var scroll := _find_scroll_container(scene)
	if scroll == null:
		printerr("Artifact review fixture could not find the control-panel scroll container")
		quit(1)
		return
	scroll.scroll_vertical = 330
	if OS.get_environment("PROJECTTOWN_CAPTURE_MODE") == "discard":
		scene.get("discard_confirmation").popup_centered(Vector2i(500, 240))
	elif OS.get_environment("PROJECTTOWN_CAPTURE_MODE") == "detail":
		scene.call("_show_artifact_preview_detail")


func _find_scroll_container(node: Node) -> ScrollContainer:
	if node is ScrollContainer:
		return node as ScrollContainer
	for child in node.get_children():
		var match := _find_scroll_container(child)
		if match != null:
			return match
	return null


func _capture() -> void:
	var output := OS.get_environment("PROJECTTOWN_VISUAL_OUTPUT")
	if output.is_empty():
		printerr("PROJECTTOWN_VISUAL_OUTPUT is required")
		quit(1)
		return
	var image := get_root().get_texture().get_image()
	var result := image.save_png(output)
	if result != OK:
		printerr("Failed to save artifact-review capture: %s" % error_string(result))
		quit(1)
		return
	print("GODOT_ARTIFACT_REVIEW_CAPTURE_OK path=%s size=%dx%d" % [output, image.get_width(), image.get_height()])
	quit(0)
