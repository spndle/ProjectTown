extends SceneTree

## Purely local contract checks: no HTTP request or provider call is made.
const APIClass := preload("res://scripts/api_client.gd")


func _initialize() -> void:
	var api: ProjectTownAPI = APIClass.new()
	if api.SETTINGS_ROUTE != "/local/settings/v1/providers/openai" or not api._valid_settings_provider("qwen") or api._valid_settings_provider("deepseek"):
		_fail("settings route changed")
		return
	var headers := api._settings_headers("test-session-token")
	if headers.size() != 2 or not headers[1].begins_with("X-ProjectTown-Settings-Token: "):
		_fail("settings token header missing")
		return
	var keep := {"base_url": "https://api.openai.com/v1", "model": "gpt-5-mini", "api_key_action": "keep", "api_key": null, "expected_revision": "rev_CANARY-nonnumeric"}
	if not api._valid_settings_body(keep):
		_fail("keep body rejected")
		return
	var qwen_keep := {"base_url": "https://workspace-example.cn-beijing.maas.aliyuncs.com/api/v1", "model": "qwen-plus", "api_key_action": "keep", "api_key": null, "expected_revision": "rev-qwen"}
	if not api._valid_settings_body(qwen_keep):
		_fail("qwen body rejected")
		return
	var invalid := keep.duplicate(true)
	invalid["api_key_action"] = "replace"
	if api._valid_settings_body(invalid):
		_fail("replace without a key accepted")
		return
	var numeric_revision := keep.duplicate(true)
	numeric_revision["expected_revision"] = 7
	if api._valid_settings_body(numeric_revision):
		_fail("numeric revision accepted")
		return
	for local_base in ["http://127.0.0.1:8000", "http://localhost:65535"]:
		api.server_base_url = local_base
		if not api._settings_backend_is_local():
			_fail("valid local settings backend rejected")
			return
	for remote_base in [
		"https://127.0.0.1:8000",
		"http://example.invalid:8000",
		"http://127.0.0.1",
		"http://127.0.0.1:0",
		"http://127.0.0.1:65536",
		"http://127.0.0.1:8000/api",
		"http://127.0.0.1:8000?x=1",
		"http://user@127.0.0.1:8000",
		"http://[::1]:8000",
	]:
		api.server_base_url = remote_base
		if api._settings_backend_is_local():
			_fail("unsafe settings backend accepted")
			return
		var callback_count := [0]
		api.settings_received.connect(func(_success: bool, _data: Dictionary, _message: String, _context: Dictionary) -> void: callback_count[0] += 1, CONNECT_ONE_SHOT)
		api.fetch_provider_settings("qwen", {"remote": true})
		if int(callback_count[0]) != 1 or api.get_child_count() != 0:
			_fail("unsafe backend constructed a request or skipped local rejection")
			return
	api.free()
	print("SETTINGS_API_CLIENT_SMOKE_OK")
	quit(0)


func _fail(message: String) -> void:
	printerr("SETTINGS_API_CLIENT_SMOKE_FAILED: %s" % message)
	quit(1)
