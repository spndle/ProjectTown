extends SceneTree

## Read-only recovery proof for a reviewable Quest already present in the target
## backend. It intentionally owns no fixture creation and never issues POST.
const APIClass := preload("res://scripts/api_client.gd")

var api: ProjectTownAPI


func _initialize() -> void:
	call_deferred("_run")


func _run() -> void:
	api = APIClass.new()
	api.server_base_url = OS.get_environment("PROJECTTOWN_TEST_URL")
	if api.server_base_url.is_empty():
		api.server_base_url = "http://127.0.0.1:8000"
	get_root().add_child(api)

	api.fetch_quests()
	var listed: Array = await api.quests_received
	if not bool(listed[0]):
		_fail("Quest list failed: %s" % str(listed[2]))
		return
	var selected: Dictionary = {}
	for raw in listed[1] as Array:
		if raw is Dictionary and str((raw as Dictionary).get("status", "")) == "waiting_user":
			selected = raw as Dictionary
			break
	if selected.is_empty():
		_fail("No waiting_user Quest is available for the read-only restore fixture")
		return
	var quest_id := str(selected.get("id", selected.get("quest_id", "")))
	if quest_id.is_empty():
		_fail("Selected restore fixture has no Quest ID")
		return

	api.reset_quest_cursor(quest_id)
	api.fetch_quest(quest_id)
	var quest_reply: Array = await api.quest_received
	if not bool(quest_reply[0]) or str(quest_reply[3]) != quest_id:
		_fail("Quest restore failed")
		return
	var quest := quest_reply[1] as Dictionary
	if str(quest.get("status", "")) != "waiting_user" or not (quest.get("pending_artifact_review", {}) is Dictionary):
		_fail("Restore fixture is no longer a reviewable Quest")
		return

	api.fetch_events(quest_id, 0)
	var events: Array = await api.events_received
	api.fetch_evidence(quest_id)
	var evidence: Array = await api.evidence_received
	api.fetch_artifacts(quest_id)
	var artifacts: Array = await api.artifacts_received
	if not bool(events[0]) or not bool(evidence[0]) or not bool(artifacts[0]) or str(events[3]) != quest_id or str(evidence[3]) != quest_id or str(artifacts[3]) != quest_id:
		_fail("Restore projections were not returned for the selected Quest")
		return
	var items: Array = (artifacts[1] as Dictionary).get("items", [])
	if items.is_empty() or not items[0] is Dictionary:
		_fail("Reviewable Quest has no artifact preview target")
		return
	api.fetch_artifact_preview(quest_id, str((items[0] as Dictionary).get("artifact_id", "")))
	var preview: Array = await api.artifact_preview_received
	if not bool(preview[0]) or str(preview[3]) != quest_id or str((preview[1] as Dictionary).get("content", "")).is_empty():
		_fail("Artifact preview was not restored")
		return
	print("RESTORE_SMOKE_OK quest=%s events=%d evidence=%d artifacts=%d" % [quest_id, (events[1] as Array).size(), (evidence[1] as Array).size(), items.size()])
	quit(0)


func _fail(message: String) -> void:
	printerr("RESTORE_SMOKE_FAILED: %s" % message)
	quit(1)
