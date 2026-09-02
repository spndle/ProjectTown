extends "res://tests/capture/capture_base.gd"

func fixture_id() -> String:
	return "settings"

func configure_fixture(scene: Control) -> void:
	scene.call("_set_settings_fixture_for_test", {
		"provider": "qwen", "base_url": "https://workspace-example.cn-beijing.maas.aliyuncs.com/api/v1",
		"model": "qwen-plus", "api_key_configured": false,
		"revision": "opaque-revision", "base_url_options": ["https://workspace-example.cn-beijing.maas.aliyuncs.com/api/v1"],
		"model_options": ["qwen-plus"], "base_url_configurable": true,
		"runtime_supported": true, "live_authorized": false,
	})
	var selector: OptionButton = scene.get("settings_provider") as OptionButton
	selector.select(1)
	scene.set("settings_selected_provider", "qwen")
	scene.call("_show_settings")
