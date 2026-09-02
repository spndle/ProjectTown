extends SceneTree

## Offline-only transport proof: no HTTPRequest is issued by this smoke.
const APIClass := preload("res://scripts/api_client.gd")


func _initialize() -> void:
	var api: ProjectTownAPI = APIClass.new()
	var encoded := api._history_request("中文 空格%_&", ["failed", "waiting user"], -4, 101)
	if str(encoded["path"]) != "/api/v2/quests?q=%E4%B8%AD%E6%96%87%20%E7%A9%BA%E6%A0%BC%25_%26&status=failed&status=waiting%20user&offset=0&limit=100":
		_fail("history query encoding or ordering changed")
		return
	if int(encoded["offset"]) != 0 or int(encoded["limit"]) != 100:
		_fail("history bounds changed")
		return
	var statuses: Array = encoded["statuses"]
	if statuses != ["failed", "waiting user"]:
		_fail("repeated status ordering changed")
		return
	api.free()
	print("HISTORY_API_CLIENT_SMOKE_OK")
	quit(0)


func _fail(message: String) -> void:
	printerr("HISTORY_API_CLIENT_SMOKE_FAILED: %s" % message)
	quit(1)
