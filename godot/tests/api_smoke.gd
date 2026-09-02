extends SceneTree

const APIClass := preload("res://scripts/api_client.gd")

var api: ProjectTownAPI
var websocket_connected := false
var websocket_first_event: Dictionary = {}
var websocket_event_source := ""
var websocket_snapshot_source := ""


func _initialize() -> void:
	call_deferred("_run")


func _run() -> void:
	api = APIClass.new()
	api.server_base_url = OS.get_environment("PROJECTTOWN_TEST_URL")
	if api.server_base_url.is_empty():
		api.server_base_url = "http://127.0.0.1:8000"
	api.websocket_state.connect(_on_websocket_state)
	api.event_received.connect(_on_websocket_event)
	api.quest_received.connect(_on_quest_received)
	get_root().add_child(api)

	api.fetch_health()
	var health: Array = await api.health_received
	if not bool(health[0]) or str((health[1] as Dictionary).get("version", "")) != "1.0.0":
		_fail("health request failed")
		return

	api.fetch_templates()
	var templates: Array = await api.templates_received
	if not bool(templates[0]) or (templates[1] as Array).size() != 3:
		_fail("template request failed")
		return

	api.create_quest("Create a verified Godot transport README", "readme_builder")
	var created: Array = await api.quest_created
	if not bool(created[0]):
		_fail("quest creation failed: %s" % str(created[2]))
		return
	var quest := created[1] as Dictionary
	if str(quest.get("status", "")) != "draft":
		_fail("quest was not created as a draft")
		return

	api.confirm_quest(
		str(quest["id"]),
		int(quest["state_version"]),
		int((quest["contract"] as Dictionary)["version"]),
		true,
		str(quest["goal"])
	)
	var confirmed: Array = await api.quest_confirmed
	if not bool(confirmed[0]):
		_fail("Goal Contract confirmation failed: %s" % str(confirmed[2]))
		return
	quest = confirmed[1] as Dictionary

	api.run_quest(str(quest["id"]), int(quest["state_version"]))
	var started: Array = await api.quest_started
	if not bool(started[0]):
		_fail("quest run failed: %s" % str(started[2]))
		return

	for _attempt in range(100):
		api.fetch_quest(str(quest["id"]))
		var fetched: Array = await api.quest_received
		if not bool(fetched[0]) or str(fetched[3]) != str(quest["id"]):
			_fail("quest polling failed: %s" % str(fetched[2]))
			return
		quest = fetched[1] as Dictionary
		if str(quest.get("status", "")) in ["completed", "failed", "budget_exhausted", "waiting_user"]:
			break
		await create_timer(0.05).timeout
	if str(quest.get("status", "")) != "waiting_user":
		_fail("quest did not reach artifact review: %s" % str(quest.get("status", "")))
		return

	api.fetch_artifacts(str(quest["id"]))
	var artifacts: Array = await api.artifacts_received
	if not bool(artifacts[0]) or (artifacts[1] as Dictionary).get("items", []).is_empty():
		_fail("reviewable artifacts were not returned")
		return
	var review := (artifacts[1] as Dictionary).get("review", {}) as Dictionary
	var item := ((artifacts[1] as Dictionary).get("items", []) as Array)[0] as Dictionary
	api.fetch_artifact_preview(str(quest["id"]), str(item["artifact_id"]))
	var preview: Array = await api.artifact_preview_received
	if not bool(preview[0]) or str((preview[1] as Dictionary).get("content", "")).is_empty():
		_fail("artifact preview was not returned")
		return
	api.review_artifacts(
		str(quest["id"]),
		str(review["review_id"]),
		str(review["manifest_hash"]),
		"retain",
		int(quest["state_version"]),
		"godot-smoke-retain"
	)
	var accepted: Array = await api.artifact_reviewed
	if not bool(accepted[0]):
		_fail("artifact retain failed: %s" % str(accepted[2]))
		return
	quest = accepted[1] as Dictionary
	if str(quest.get("status", "")) != "completed":
		_fail("retained quest did not complete: %s" % str(quest.get("status", "")))
		return

	api.fetch_events(str(quest["id"]), 0)
	var events: Array = await api.events_received
	if not bool(events[0]) or (events[1] as Array).is_empty() or str(events[3]) != str(quest["id"]):
		_fail("events were not returned")
		return
	api.fetch_evidence(str(quest["id"]))
	var evidence: Array = await api.evidence_received
	if not bool(evidence[0]) or (evidence[1] as Array).is_empty() or str(evidence[3]) != str(quest["id"]):
		_fail("evidence was not returned")
		return

	api.last_sequence = 0
	websocket_snapshot_source = ""
	api.connect_events(str(quest["id"]))
	for _attempt in range(100):
		if websocket_connected and not websocket_first_event.is_empty():
			break
		await create_timer(0.02).timeout
	if not websocket_connected or websocket_snapshot_source != str(quest["id"]) or websocket_event_source != str(quest["id"]) or int(websocket_first_event.get("sequence", 0)) != 1:
		_fail("WebSocket snapshot/replay did not deliver sequence 1")
		return

	print(
		"GODOT_API_SMOKE_OK quest=%s events=%d evidence=%d" % [
			quest["id"],
			(events[1] as Array).size(),
			(evidence[1] as Array).size(),
		]
	)
	api.disconnect_events()
	quit(0)


func _on_websocket_state(connected: bool, _message: String, _source_quest_id: String) -> void:
	websocket_connected = connected


func _on_quest_received(_success: bool, _quest: Dictionary, _message: String, source_quest_id: String) -> void:
	websocket_snapshot_source = source_quest_id


func _on_websocket_event(event: Dictionary, source_quest_id: String) -> void:
	if websocket_first_event.is_empty():
		websocket_first_event = event
		websocket_event_source = source_quest_id


func _fail(message: String) -> void:
	printerr("GODOT_API_SMOKE_FAILED: %s" % message)
	if api != null:
		api.disconnect_events()
	quit(1)
