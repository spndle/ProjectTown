extends "res://tests/capture/capture_base.gd"

func fixture_id() -> String:
	return "failure"


func configure_fixture(scene: Control) -> void:
	scene.active_quest_id = "q_fixture_failure_001"
	scene.active_status = "failed"
	scene.goal_input.text = "固定失败状态的离线 Quest"
	scene.quest_id_label.text = "Quest · q_fixture_failure_001"
	scene.status_badge.text = "FAILED"
	scene.progress_bar.value = 67.0
	scene.current_step_label.text = "任务未完成：工具执行未产生可验证成果"
	scene.failure_detail_label.text = "[color=#bd5b55]TOOL_FAILED[/color]\n固定夹具：本地命令返回非零，未提交任何外部请求。"
	scene.town_view.set_quest_state("failed", 0.67, scene.goal_input.text)
	scene.call("_update_onboarding", "failed", false)
	scene.call("_update_control_button")
	scene.call("_update_primary_action")
	scene.backend_label.text = "离线夹具 · 固定昼间"
