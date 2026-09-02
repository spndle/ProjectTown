extends "res://tests/capture/capture_base.gd"

func fixture_id() -> String:
	return "history"


func configure_fixture(scene: Control) -> void:
	var items := [
		{"id": "q_fixture_history_failed", "status": "failed", "goal": "固定失败 Quest 历史条目", "error": {"code": "TOOL_FAILED"}},
		{"id": "q_fixture_history_review", "status": "waiting_user", "goal": "固定成果审核历史条目", "artifact_review_required": true},
	]
	scene.call("_request_history_page", 0)
	scene.call("_on_history_received", true, items, 2, "", {"generation": scene.history_generation, "offset": 0, "limit": 20})
	scene.history_status_label.text = "固定离线历史 · 共 2 条"
	scene.backend_label.text = "离线夹具 · 固定昼间"
