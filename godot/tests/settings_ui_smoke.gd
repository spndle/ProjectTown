extends SceneTree

const MAIN_SCENE := preload("res://main.tscn")


func _initialize() -> void:
	call_deferred("_run")


func _run() -> void:
	var scene: Control = MAIN_SCENE.instantiate()
	scene.set("transport_enabled", false)
	root.add_child(scene)
	await process_frame
	var baseline_id := str(scene.get("active_quest_id"))
	var baseline_state := str(scene.get("active_status"))
	var fixture := {
		"openai": {"provider": "openai", "base_url": "https://api.openai.com/v1", "model": "gpt-5-mini", "api_key_configured": true, "revision": "rev_CANARY-nonnumeric", "base_url_options": ["https://api.openai.com/v1"], "model_options": ["gpt-5-mini", "gpt-4.1-mini"], "runtime_supported": true},
		"qwen": {"provider": "qwen", "base_url": "https://workspace-example.cn-beijing.maas.aliyuncs.com/api/v1", "model": "qwen-plus", "api_key_configured": false, "revision": "rev-QWEN", "base_url_options": ["https://workspace-example.cn-beijing.maas.aliyuncs.com/api/v1"], "model_options": ["qwen-plus"], "base_url_configurable": true, "runtime_supported": true, "live_authorized": false},
	}
	scene.call("_set_settings_fixture_for_test", fixture)
	scene.call("_show_settings")
	await process_frame
	await process_frame
	var dialog: Window = scene.get("settings_dialog") as Window
	var key_input: LineEdit = scene.get("settings_api_key") as LineEdit
	var model: LineEdit = scene.get("settings_model") as LineEdit
	var provider: OptionButton = scene.get("settings_provider") as OptionButton
	var base_url_input: LineEdit = scene.get("settings_base_url") as LineEdit
	var save: Button = scene.get("settings_save_button") as Button
	if dialog == null or key_input == null or model == null or provider == null or base_url_input == null or save == null:
		_fail("settings controls were not created")
		return
	if not dialog.exclusive or not dialog.visible or not key_input.secret or key_input.text != "":
		_fail("settings dialog is not exclusive or key input is not masked/empty")
		return
	if not base_url_input.editable or not model.editable or model.text != "gpt-5-mini" or save.disabled:
		_fail("redacted fixture did not populate editable text inputs")
		return
	base_url_input.text = "  https://api.openai.com/v1  "
	model.text = "  gpt-5-mini  "
	var request_body: Dictionary = scene.call("_settings_request_body") as Dictionary
	if str(request_body.get("expected_revision", "")) != "rev_CANARY-nonnumeric":
		_fail("opaque revision did not round trip into settings PUT body")
		return
	if request_body.get("base_url", "") != "https://api.openai.com/v1" or request_body.get("model", "") != "gpt-5-mini":
		_fail("text inputs were not trimmed into the settings PUT body")
		return
	for width in [900.0, 1280.0, 1920.0]:
		scene.size = Vector2(width, 720.0)
		scene.call("_apply_responsive_layout")
		scene.call("_show_settings")
		await process_frame
		if float(dialog.position.x + dialog.size.x) > scene.get_global_rect().end.x + 0.5:
			_fail("settings dialog exceeds scene at width %.0f" % width)
			return
	provider.select(1)
	scene.call("_on_settings_provider_selected", 1)
	await process_frame
	await process_frame
	if str(scene.get("settings_selected_provider")) != "qwen" or not base_url_input.visible or not base_url_input.editable or not model.editable or base_url_input.text.is_empty() or model.text != "qwen-plus":
		_fail("qwen provider did not load editable bounded URL and model inputs")
		return
	base_url_input.text = "  https://workspace-example.cn-beijing.maas.aliyuncs.com/api/v1  "
	model.text = "  qwen-plus  "
	var qwen_body: Dictionary = scene.call("_settings_request_body") as Dictionary
	if qwen_body.get("base_url", "") != "https://workspace-example.cn-beijing.maas.aliyuncs.com/api/v1" or qwen_body.get("model", "") != "qwen-plus":
		_fail("qwen request body is not provider scoped")
		return
	key_input.text = "test-only-key"
	scene.call("_save_settings")
	if key_input.text != "":
		_fail("key input was retained after save")
		return
	scene.call("_on_settings_saved", true, fixture["qwen"], "", {"generation": int(scene.get("settings_generation")), "provider": "qwen"})
	if str(scene.get("settings_revision")) != "rev-QWEN":
		_fail("opaque revision did not survive save response")
		return
	base_url_input.text = "typed-but-not-saved"
	model.text = "typed-but-not-saved"
	provider.select(0)
	scene.call("_on_settings_provider_selected", 0)
	await process_frame
	await process_frame
	if base_url_input.text != "https://api.openai.com/v1" or model.text != "gpt-5-mini" or key_input.text != "":
		_fail("provider switch retained typed or secret state")
		return
	scene.call("_set_settings_fixture_for_test", {})
	scene.call("_show_settings")
	await process_frame
	if not base_url_input.visible or not base_url_input.editable or not model.editable or not key_input.editable or not save.disabled:
		_fail("unavailable local settings did not leave inputs editable with save disabled")
		return
	if "不可用" not in str((scene.get("settings_status_label") as Label).text) or "无法保存" not in str((scene.get("settings_status_label") as Label).text):
		_fail("unavailable local settings status was not explicit")
		return
	key_input.text = "test-only-key"
	scene.call("_close_settings_dialog")
	if key_input.text != "" or key_input.editable == false or dialog.visible:
		_fail("closing settings retained key state")
		return
	if str(scene.get("active_quest_id")) != baseline_id or str(scene.get("active_status")) != baseline_state:
		_fail("settings changed Quest state")
		return
	var stale := {"generation": int(scene.get("settings_generation")) - 1}
	scene.call("_on_settings_received", false, {}, "", stale)
	if dialog.visible:
		_fail("stale response reopened closed modal")
		return
	print("SETTINGS_UI_SMOKE_OK")
	scene.queue_free()
	quit(0)


func _fail(message: String) -> void:
	printerr("SETTINGS_UI_SMOKE_FAILED: %s" % message)
	quit(1)
