extends "res://tests/capture/capture_base.gd"

func fixture_id() -> String:
	return "artifact_review_waiting_user"


func configure_fixture(scene: Control) -> void:
	scene.active_quest_id = "q_fixture_artifact_review_001"
	scene.active_status = "waiting_user"
	scene.artifact_review_pending = true
	scene.result_review_key = "review_fixture_001"
	scene.result_manifest_hash = "f1a7c0de"
	scene.goal_input.text = "生成固定的项目简报成果并等待审核"
	scene.quest_id_label.text = "Quest · q_fixture_artifact_review_001"
	scene.status_badge.text = "WAITING_USER"
	scene.progress_bar.value = 100.0
	scene.current_step_label.text = "请预览成果并选择保留或丢弃"
	scene.onboarding_step_label.text = "第 4 步 · 预览成果并作出选择"
	scene.onboarding_help_label.text = "固定离线成果已验证；请逐个查看后作出选择。"
	scene.result_state_label.text = "验收已通过。请逐个查看成果，再选择保留或丢弃。"
	scene.result_select.clear()
	scene.result_select.add_item("fixtures/project_brief.md")
	scene.result_select.set_item_metadata(0, "artifact_fixture_brief")
	scene.result_select.disabled = false
	scene.active_artifact_id = "artifact_fixture_brief"
	scene.active_preview_path = "fixtures/project_brief.md"
	scene.active_preview_hash = "f1a7c0de"
	scene.active_preview_content = "# 固定项目简报\n\n目标：将个人目标变为可追踪、可验收的 Quest。\n\n- 已生成里程碑\n- 已完成本地验收\n- 等待用户保留或丢弃"
	scene.preview_ready = true
	scene.result_preview.text = scene.active_preview_content
	scene.artifact_preview_detail_button.disabled = false
	scene.keep_result_button.disabled = false
	scene.discard_result_button.disabled = false
	scene.town_view.set_quest_state("waiting_user", 1.0, scene.goal_input.text)
	scene.call("_update_control_button")
	scene.call("_update_primary_action")
	scene.backend_label.text = "离线夹具 · 固定昼间"


func settle_fixture(scene: Control) -> void:
	var scroll := _find_scroll_container(scene)
	if scroll == null:
		_fail("artifact review fixture could not find the control-panel scroll container")
		return
	# The result selector, preview, and retain/discard buttons are below the
	# default right-panel fold at the supported 1280x720 viewport.
	scroll.scroll_vertical = 620


func _find_scroll_container(node: Node) -> ScrollContainer:
	if node is ScrollContainer:
		return node as ScrollContainer
	for child in node.get_children():
		var match := _find_scroll_container(child)
		if match != null:
			return match
	return null
