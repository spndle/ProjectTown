extends SceneTree

const MAIN_SCENE := preload("res://main.tscn")


func _initialize() -> void:
	call_deferred("_run")


func _run() -> void:
	var scene: Control = MAIN_SCENE.instantiate()
	scene.set("transport_enabled", false)
	root.add_child(scene)
	await process_frame
	var first := {"id": "qv1_history_a", "status": "failed", "goal": "Failure navigation", "updated_at": "now", "error": {"code": "TOOL_FAILED"}}
	var second := {"id": "qv1_history_b", "status": "waiting_user", "goal": "Review artifacts", "updated_at": "now", "artifact_review_required": true}
	scene.call("_request_history_page", 0)
	scene.call("_on_history_received", true, [first, second], 22, "", {"generation": scene.get("history_generation"), "offset": 0, "limit": 20})
	await process_frame
	var selector: OptionButton = scene.get("history_select") as OptionButton
	var page: Label = scene.get("history_page_label") as Label
	if selector == null or page == null or selector.item_count != 2:
		_fail("history page controls were not populated")
		return
	if "待成果审核" not in selector.get_item_text(1) or "共 22 条" not in page.text:
		_fail("history status summary missing")
		return
	scene.call("_on_history_received", true, [{"id": "stale"}], 1, "", {"generation": int(scene.get("history_generation")) - 1})
	if selector.item_count != 2:
		_fail("stale history response replaced current page")
		return
	scene.set("active_quest_id", "qv1_history_a")
	scene.call("_on_failure_received", true, {"summary": {"category": "tool_execution", "code": "TOOL_FAILED", "message": "A tool operation did not complete."}, "navigation": {"milestone_id": "one", "evidence_ids": ["e1"]}}, "", "qv1_history_a")
	var detail: RichTextLabel = scene.get("failure_detail_label") as RichTextLabel
	if detail == null or "TOOL_FAILED" not in detail.text:
		_fail("safe failure detail did not render")
		return
	print("HISTORY_UI_SMOKE_OK")
	scene.queue_free()
	quit(0)


func _fail(message: String) -> void:
	printerr("HISTORY_UI_SMOKE_FAILED: %s" % message)
	quit(1)
