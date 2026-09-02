extends SceneTree

const MAIN_SCENE := preload("res://main.tscn")
const CASES := [
	{"status": "shadow_observed_created", "needle": "兼容性影子记录", "color": Color("#5aa66f")},
	{"status": "shadow_existing_unchanged", "needle": "前已存在且未变化", "color": Color("#b8c4ca")},
	{"status": "shadow_unobserved_created", "needle": "未观测或外部文件变化", "color": Color("#d79a48")},
	{"status": "shadow_external_drift", "needle": "未观测或外部文件变化", "color": Color("#d79a48")},
	{"status": "legacy_unobserved", "needle": "历史 Quest", "color": Color("#b8c4ca")},
	{"status": "unrecoverable_final_hash_mismatch", "needle": "不影响当前成果预览和你的保留或丢弃确认", "color": Color("#d79a48")},
	{"status": "", "needle": "兼容 manifest", "color": Color("#b8c4ca")},
	{"status": "future_provider_claim", "needle": "未知 provenance 状态", "color": Color("#d79a48")},
]


func _initialize() -> void:
	call_deferred("_run")


func _run() -> void:
	var scene: Control = MAIN_SCENE.instantiate()
	scene.set("transport_enabled", false)
	root.add_child(scene)
	await process_frame
	await process_frame
	var label: Label = scene.get("artifact_provenance_label") as Label
	var keep: Button = scene.get("keep_result_button") as Button
	var discard: Button = scene.get("discard_result_button") as Button
	if label == null or keep == null or discard == null:
		_fail("artifact provenance controls were not created")
		return
	scene.set("active_quest_id", "qv1_provenance_ui")
	scene.set("active_status", "waiting_user")
	scene.set("result_review_key", "review_provenance_ui")
	scene.set("result_manifest_hash", "fixturehash")
	scene.set("active_artifact_id", "artifact_provenance")
	scene.set("active_preview_hash", "fixturehash")
	scene.set("preview_ready", true)
	var base_item := {"artifact_id": "artifact_provenance", "path": "deliverables/long-audit-file.md", "hash": "fixturehash"}
	scene.call("_on_artifacts_received", true, {"items": [base_item], "review": {"review_id": "review_provenance_ui", "manifest_hash": "fixturehash"}, "artifact_disposition": "pending"}, "", "qv1_provenance_ui")
	var baseline_keep := keep.disabled
	var baseline_discard := discard.disabled
	if baseline_keep or baseline_discard:
		_fail("baseline artifact was not in a real review-enabled state")
		return
	for test_case in CASES:
		var presentation: Dictionary = scene.call("_provenance_presentation", str(test_case["status"]))
		if str(test_case["needle"]) not in str(presentation.get("text", "")):
			_fail("status %s has unexpected text" % str(test_case["status"]))
			return
		if presentation.get("color") != test_case["color"]:
			_fail("status %s has unexpected color" % str(test_case["status"]))
			return
	var provenance_item := {"artifact_id": "artifact_provenance", "path": "deliverables/long-audit-file.md", "hash": "fixturehash", "provenance_status": "unrecoverable_final_hash_mismatch"}
	scene.call("_on_artifacts_received", true, {"items": [provenance_item], "review": {"review_id": "review_provenance_ui", "manifest_hash": "fixturehash"}, "artifact_disposition": "pending"}, "", "qv1_provenance_ui")
	if not label.visible or "不影响当前成果预览" not in label.text:
		_fail("selected artifact did not update the provenance label")
		return
	if keep.disabled != baseline_keep or discard.disabled != baseline_discard or keep.disabled or discard.disabled:
		_fail("provenance presentation changed retain/discard button state")
		return
	for width in [900.0, 1280.0, 1920.0]:
		scene.size = Vector2(width, 720.0)
		scene.call("_apply_responsive_layout")
		await process_frame
		var panel: PanelContainer = scene.get("control_panel") as PanelContainer
		if panel == null or label.get_global_rect().end.x > panel.get_global_rect().end.x + 0.5:
			_fail("provenance label exceeds control panel at width %.0f" % width)
			return
	print("ARTIFACT_PROVENANCE_UI_SMOKE_OK cases=%d" % CASES.size())
	scene.queue_free()
	quit(0)


func _fail(message: String) -> void:
	printerr("ARTIFACT_PROVENANCE_UI_SMOKE_FAILED: %s" % message)
	quit(1)
