extends "res://tests/capture/capture_base.gd"

func fixture_id() -> String:
	return "tutorial"


func configure_fixture(scene: Control) -> void:
	scene.goal_input.text = "学习固定的离线 Quest 工作流"
	scene.backend_label.text = "离线夹具 · 固定昼间"
	scene.call("_show_tutorial")
