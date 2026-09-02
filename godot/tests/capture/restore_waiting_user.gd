extends "res://tests/capture/capture_base.gd"

func fixture_id() -> String:
	return "restore_waiting_user"


func configure_fixture(scene: Control) -> void:
	var quest_id := "q_fixture_restore_waiting_001"
	scene.call("_request_history_page", 0)
	scene.call("_on_history_received", true, [{"id": quest_id, "status": "waiting_user", "goal": "恢复固定的等待用户决策 Quest"}], 1, "", {"generation": scene.history_generation, "offset": 0, "limit": 20})
	scene.active_quest_id = quest_id
	scene.active_status = "waiting_user"
	scene.goal_input.text = "恢复固定的等待用户决策 Quest"
	scene.quest_id_label.text = "Quest · %s" % quest_id
	scene.status_badge.text = "WAITING_USER"
	scene.progress_bar.value = 50.0
	scene.current_step_label.text = "等待用户决策"
	scene.onboarding_step_label.text = "等待下一步操作"
	scene.onboarding_help_label.text = "固定恢复夹具：请选择批准、修改目标或拒绝任务。"
	scene.town_view.set_quest_state("waiting_user", 0.5, scene.goal_input.text)
	scene.call("_update_control_button")
	scene.call("_update_primary_action")
	scene.history_status_label.text = "已恢复固定本地 Quest；未发起网络请求。"
	scene.backend_label.text = "离线夹具 · 固定昼间"
