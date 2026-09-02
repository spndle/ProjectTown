extends "res://tests/capture/capture_base.gd"

func fixture_id() -> String:
	return "main"


func configure_fixture(scene: Control) -> void:
	scene.goal_input.text = "将个人目标拆解为可验证的本地 Quest"
	scene.workspace_input.text = "fixtures/main-workspace"
	scene.backend_label.text = "离线夹具 · 固定昼间"
	scene.town_view.set_quest_state("idle", 0.0, scene.goal_input.text)
