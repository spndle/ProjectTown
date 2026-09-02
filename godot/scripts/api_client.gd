extends Node
class_name ProjectTownAPI

## Product v1 Goal Contract transport. Events are ordered at-least-once; the
## client deduplicates by sequence and falls back to REST when the socket fails.
signal health_received(success: bool, data: Dictionary, message: String)
signal templates_received(success: bool, items: Array, message: String)
signal quests_received(success: bool, items: Array, message: String)
# History is deliberately separate from the v1-compatible quests_received
# signal: callers use request_context to discard an out-of-date response.
signal quest_history_loading(request_context: Dictionary)
signal quest_history_received(success: bool, items: Array, total: int, message: String, request_context: Dictionary)
signal quest_history_error(message: String, request_context: Dictionary)
signal quest_failure_received(success: bool, data: Dictionary, message: String, source_quest_id: String)
signal quest_failure_error(message: String, source_quest_id: String)
signal quest_created(success: bool, quest: Dictionary, message: String)
signal quest_confirmed(success: bool, quest: Dictionary, message: String, source_quest_id: String)
signal quest_started(success: bool, data: Dictionary, message: String, source_quest_id: String)
signal quest_controlled(success: bool, data: Dictionary, message: String, source_quest_id: String)
signal decision_submitted(success: bool, data: Dictionary, message: String, source_quest_id: String)
signal quest_received(success: bool, quest: Dictionary, message: String, source_quest_id: String)
signal events_received(success: bool, items: Array, message: String, source_quest_id: String)
signal evidence_received(success: bool, items: Array, message: String, source_quest_id: String)
signal artifacts_received(success: bool, data: Dictionary, message: String, source_quest_id: String)
signal artifact_preview_received(success: bool, data: Dictionary, message: String, source_quest_id: String)
signal artifact_reviewed(success: bool, data: Dictionary, message: String, source_quest_id: String)
signal settings_received(success: bool, data: Dictionary, message: String, request_context: Dictionary)
signal settings_saved(success: bool, data: Dictionary, message: String, request_context: Dictionary)
signal event_received(event: Dictionary, source_quest_id: String)
signal websocket_state(connected: bool, message: String, source_quest_id: String)

@export var server_base_url := "http://127.0.0.1:8000"
@export_range(1.0, 60.0, 0.5) var request_timeout_seconds := 12.0
var socket: WebSocketPeer = WebSocketPeer.new()
var websocket_quest_id := ""
var cursor_quest_id := ""
var last_sequence := 0
var _requests: Array[HTTPRequest] = []
const SETTINGS_ROUTE := "/local/settings/v1/providers/openai"
const SETTINGS_PROVIDER_ROUTES := {
	"openai": "/local/settings/v1/providers/openai",
	"qwen": "/local/settings/v1/providers/qwen",
}
const SETTINGS_TOKEN_PATH := "res://../.secrets/projecttown-settings-session.token"
const SETTINGS_TOKEN_MAX_LENGTH := 512

func _process(_delta: float) -> void:
	if websocket_quest_id.is_empty():
		return
	socket.poll()
	if socket.get_ready_state() == WebSocketPeer.STATE_OPEN:
		while socket.get_available_packet_count() > 0:
			var value: Variant = JSON.parse_string(socket.get_packet().get_string_from_utf8())
			if value is Dictionary:
				_handle_socket_message(value as Dictionary)
	elif socket.get_ready_state() == WebSocketPeer.STATE_CLOSED:
		websocket_state.emit(false, "WebSocket disconnected; using REST polling", websocket_quest_id)
		websocket_quest_id = ""

func _new_request(callback: Callable) -> HTTPRequest:
	var request := HTTPRequest.new()
	request.timeout = request_timeout_seconds
	request.accept_gzip = true
	add_child(request)
	request.request_completed.connect(_on_request_completed.bind(request, callback))
	_requests.append(request)
	return request

func _on_request_completed(
	result: int,
	code: int,
	headers: PackedStringArray,
	body: PackedByteArray,
	request: HTTPRequest,
	callback: Callable
) -> void:
	if callback.is_valid():
		callback.call(result, code, headers, body)
	_requests.erase(request)
	request.queue_free()

func _request(path: String, method: HTTPClient.Method = HTTPClient.METHOD_GET, body: Dictionary = {}, callback: Callable = Callable()) -> void:
	var request := _new_request(callback)
	var headers := ["Accept: application/json"]
	var payload := ""
	if method != HTTPClient.METHOD_GET:
		headers.append("Content-Type: application/json")
		payload = JSON.stringify(body)
	var error := request.request(_url(path), headers, method, payload)
	if error != OK:
		if callback.is_valid():
			callback.call(-1, 0, PackedStringArray(), PackedByteArray())
		_requests.erase(request)
		request.queue_free()


## The settings control plane deliberately remains local to ProjectTown's
## backend. It never contacts a model provider from the Godot client.
func fetch_openai_settings(request_context: Dictionary = {}) -> void:
	fetch_provider_settings("openai", request_context)


func fetch_provider_settings(provider: String, request_context: Dictionary = {}) -> void:
	if not _valid_settings_provider(provider):
		settings_received.emit(false, {}, "本地设置不可用", request_context)
		return
	if not _settings_backend_is_local():
		settings_received.emit(false, {}, "本地设置不可用", request_context)
		return
	var token := _settings_token()
	if token.is_empty():
		settings_received.emit(false, {}, "本地设置不可用", request_context)
		return
	_settings_request(provider, HTTPClient.METHOD_GET, {}, token, Callable(self, "_on_settings_received").bind(request_context))


func save_openai_settings(body: Dictionary, request_context: Dictionary = {}) -> void:
	save_provider_settings("openai", body, request_context)


func save_provider_settings(provider: String, body: Dictionary, request_context: Dictionary = {}) -> void:
	if not _valid_settings_provider(provider):
		settings_saved.emit(false, {}, "设置内容无效", request_context)
		return
	if not _valid_settings_body(body):
		settings_saved.emit(false, {}, "设置内容无效", request_context)
		return
	if not _settings_backend_is_local():
		settings_saved.emit(false, {}, "本地设置不可用", request_context)
		return
	var token := _settings_token()
	if token.is_empty():
		settings_saved.emit(false, {}, "本地设置不可用", request_context)
		return
	_settings_request(provider, HTTPClient.METHOD_PUT, body, token, Callable(self, "_on_settings_saved").bind(request_context))


func _settings_token() -> String:
	var file := FileAccess.open(SETTINGS_TOKEN_PATH, FileAccess.READ)
	if file == null:
		return ""
	var length := file.get_length()
	if length <= 0 or length > SETTINGS_TOKEN_MAX_LENGTH:
		file.close()
		return ""
	var token := file.get_buffer(length).get_string_from_utf8().strip_edges()
	file.close()
	return token if not token.is_empty() and token.length() <= SETTINGS_TOKEN_MAX_LENGTH else ""


## Settings session tokens are local-only credentials. Unlike ordinary Quest
## traffic, their control plane must never follow the configurable backend URL
## to a remote host, HTTPS endpoint, or a URL containing a path/query/fragment.
func _settings_backend_is_local() -> bool:
	var matcher := RegEx.new()
	if matcher.compile("^http://(127\\.0\\.0\\.1|localhost):([1-9][0-9]{0,4})$") != OK:
		return false
	var match := matcher.search(server_base_url)
	return match != null and int(match.get_string(2)) <= 65535


func _valid_settings_body(body: Dictionary) -> bool:
	if not body.has_all(["base_url", "model", "api_key_action", "api_key", "expected_revision"]):
		return false
	if not (body["base_url"] is String) or not (body["model"] is String):
		return false
	var action := str(body["api_key_action"])
	if action not in ["keep", "replace", "clear"]:
		return false
	if not (body["expected_revision"] is String) or str(body["expected_revision"]).is_empty() or str(body["expected_revision"]).length() > 256:
		return false
	var key: Variant = body["api_key"]
	if action == "replace":
		return key is String and not str(key).strip_edges().is_empty() and str(key).length() <= 4096
	return key == null


func _valid_settings_provider(provider: String) -> bool:
	return provider in SETTINGS_PROVIDER_ROUTES


func _settings_request(provider: String, method: HTTPClient.Method, body: Dictionary, token: String, callback: Callable) -> void:
	if not _valid_settings_provider(provider):
		callback.call(-1, 0, PackedStringArray(), PackedByteArray())
		return
	if not _settings_backend_is_local():
		callback.call(-1, 0, PackedStringArray(), PackedByteArray())
		return
	var request := _new_request(callback)
	var headers := _settings_headers(token)
	var payload := ""
	if method != HTTPClient.METHOD_GET:
		headers.append("Content-Type: application/json")
		payload = JSON.stringify(body)
	var error := request.request(_url(str(SETTINGS_PROVIDER_ROUTES[provider])), headers, method, payload)
	if error != OK:
		callback.call(-1, 0, PackedStringArray(), PackedByteArray())
		_requests.erase(request)
		request.queue_free()


func _settings_headers(token: String) -> PackedStringArray:
	return PackedStringArray(["Accept: application/json", "X-ProjectTown-Settings-Token: %s" % token])

func fetch_health() -> void:
	_request("/health", HTTPClient.METHOD_GET, {}, Callable(self, "_on_health"))

func fetch_templates() -> void:
	_request("/api/v2/templates", HTTPClient.METHOD_GET, {}, Callable(self, "_on_templates"))


func fetch_quests() -> void:
	_request("/api/v2/quests", HTTPClient.METHOD_GET, {}, Callable(self, "_on_quests"))

func fetch_quest_history(
	q: String = "",
	statuses: Array[String] = [],
	offset: int = 0,
	limit: int = 20,
	request_context: Dictionary = {}
) -> void:
	var history := _history_request(q, statuses, offset, limit)
	var context := request_context.duplicate(true)
	context["q"] = history["q"]
	context["statuses"] = history["statuses"]
	context["offset"] = history["offset"]
	context["limit"] = history["limit"]
	quest_history_loading.emit(context)
	_request(
		str(history["path"]),
		HTTPClient.METHOD_GET,
		{},
		Callable(self, "_on_quest_history").bind(context)
	)

func _history_request(q: String, statuses: Array[String], offset: int, limit: int) -> Dictionary:
	var normalized_offset := maxi(offset, 0)
	var normalized_limit := clampi(limit, 1, 100)
	var normalized_statuses: Array[String] = []
	var query: Array[String] = []
	if not q.is_empty():
		query.append("q=%s" % q.uri_encode())
	for status in statuses:
		if not status.is_empty():
			normalized_statuses.append(status)
			query.append("status=%s" % status.uri_encode())
	query.append("offset=%d" % normalized_offset)
	query.append("limit=%d" % normalized_limit)
	return {
		"path": "/api/v2/quests?%s" % "&".join(query),
		"q": q,
		"statuses": normalized_statuses,
		"offset": normalized_offset,
		"limit": normalized_limit,
	}

func fetch_failure(quest_id: String) -> void:
	_request(
		"/api/v2/quests/%s/failure" % quest_id.uri_encode(),
		HTTPClient.METHOD_GET,
		{},
		Callable(self, "_on_failure").bind(quest_id)
	)

func create_quest(
	goal: String,
	template_id: String,
	_workspace: String = "",
	artifact_review_required: bool = true
) -> void:
	_request(
		"/api/v2/quests",
		HTTPClient.METHOD_POST,
		{
			"goal": goal,
			"template_id": template_id,
			"artifact_review_required": artifact_review_required,
		},
		Callable(self, "_on_create")
	)

func confirm_quest(
	quest_id: String,
	expected_state_version: int,
	expected_contract_version: int = 1,
	approved: bool = true,
	goal: String = ""
) -> void:
	var body := {
		"expected_state_version": expected_state_version,
		"expected_contract_version": expected_contract_version,
		"approved": approved,
	}
	if not goal.strip_edges().is_empty():
		body["goal"] = goal.strip_edges()
	_request("/api/v2/quests/%s/confirm" % quest_id.uri_encode(), HTTPClient.METHOD_POST, body, Callable(self, "_on_confirm").bind(quest_id))

func run_quest(quest_id: String, expected_state_version: int = 0) -> void:
	var body := {} if expected_state_version <= 0 else {"expected_state_version": expected_state_version}
	_request("/api/v2/quests/%s/run" % quest_id.uri_encode(), HTTPClient.METHOD_POST, body, Callable(self, "_on_run").bind(quest_id))

func pause_quest(quest_id: String, expected_state_version: int = 0) -> void:
	_control(quest_id, "pause", expected_state_version)

func resume_quest(quest_id: String, expected_state_version: int = 0) -> void:
	_control(quest_id, "resume", expected_state_version)

func _control(quest_id: String, action: String, expected_state_version: int) -> void:
	var body := {} if expected_state_version <= 0 else {"expected_state_version": expected_state_version}
	_request("/api/v2/quests/%s/%s" % [quest_id.uri_encode(), action], HTTPClient.METHOD_POST, body, Callable(self, "_on_control").bind(action, quest_id))

func fetch_quest(quest_id: String) -> void:
	_request("/api/v2/quests/%s" % quest_id.uri_encode(), HTTPClient.METHOD_GET, {}, Callable(self, "_on_quest").bind(quest_id))

func fetch_events(quest_id: String, after_sequence: int = 0) -> void:
	_request("/api/v2/quests/%s/events?after_sequence=%d" % [quest_id.uri_encode(), after_sequence], HTTPClient.METHOD_GET, {}, Callable(self, "_on_events").bind(quest_id))

func fetch_evidence(quest_id: String) -> void:
	_request("/api/v2/quests/%s/evidence" % quest_id.uri_encode(), HTTPClient.METHOD_GET, {}, Callable(self, "_on_evidence").bind(quest_id))

func fetch_artifacts(quest_id: String) -> void:
	_request(
		"/api/v2/quests/%s/artifacts" % quest_id.uri_encode(),
		HTTPClient.METHOD_GET,
		{},
		Callable(self, "_on_artifacts").bind(quest_id)
	)

func fetch_artifact_preview(quest_id: String, artifact_id: String) -> void:
	_request(
		"/api/v2/quests/%s/artifacts/%s/preview" % [quest_id.uri_encode(), artifact_id.uri_encode()],
		HTTPClient.METHOD_GET,
		{},
		Callable(self, "_on_artifact_preview").bind(quest_id)
	)

func review_artifacts(
	quest_id: String,
	review_id: String,
	manifest_hash: String,
	decision: String,
	expected_state_version: int,
	idempotency_key: String,
	note: String = ""
) -> void:
	_request(
		"/api/v2/quests/%s/artifacts/review" % quest_id.uri_encode(),
		HTTPClient.METHOD_POST,
		{
			"review_id": review_id,
			"manifest_hash": manifest_hash,
			"decision": decision,
			"expected_state_version": expected_state_version,
			"idempotency_key": idempotency_key,
			"note": note,
		},
		Callable(self, "_on_artifact_review").bind(quest_id)
	)

func submit_decision(
	quest_id: String,
	kind: String,
	expected_state_version: int,
	note: String,
	contract_patch: Dictionary = {}
) -> void:
	_request(
		"/api/v2/quests/%s/decisions" % quest_id.uri_encode(),
		HTTPClient.METHOD_POST,
		{
			"kind": kind,
			"expected_state_version": expected_state_version,
			"note": note,
			"contract_patch": contract_patch,
		},
		Callable(self, "_on_decision").bind(quest_id)
	)

func fetch_traces(quest_id: String) -> void:
	fetch_events(quest_id, last_sequence if cursor_quest_id == quest_id else 0)

func reset_quest_cursor(quest_id: String) -> void:
	cursor_quest_id = quest_id
	last_sequence = 0

func is_websocket_connected() -> bool:
	return not websocket_quest_id.is_empty() and socket.get_ready_state() == WebSocketPeer.STATE_OPEN

func connect_events(quest_id: String) -> void:
	disconnect_events()
	if cursor_quest_id != quest_id:
		reset_quest_cursor(quest_id)
	socket = WebSocketPeer.new()
	websocket_quest_id = quest_id
	var ws_url := server_base_url.replace("https://", "wss://").replace("http://", "ws://")
	var error := socket.connect_to_url("%s/ws/quests/%s?resume_after=%d" % [ws_url.trim_suffix("/"), quest_id.uri_encode(), last_sequence])
	if error != OK:
		websocket_quest_id = ""
		websocket_state.emit(false, "WebSocket unavailable; using REST polling", quest_id)

func disconnect_events() -> void:
	if socket.get_ready_state() != WebSocketPeer.STATE_CLOSED:
		socket.close()
	websocket_quest_id = ""

func _handle_socket_message(data: Dictionary) -> void:
	if str(data.get("type", "")) == "snapshot":
		var snapshot: Dictionary = data.get("quest", {})
		quest_received.emit(true, snapshot, "", websocket_quest_id)
		websocket_state.emit(true, "WebSocket connected", websocket_quest_id)
		return
	var event: Dictionary = data.get("event", data)
	var sequence := int(event.get("sequence", 0))
	if sequence <= last_sequence:
		return
	last_sequence = sequence
	event_received.emit(event, websocket_quest_id)

func _decode(body: PackedByteArray) -> Dictionary:
	var value: Variant = JSON.parse_string(body.get_string_from_utf8())
	return value as Dictionary if value is Dictionary else {}

func _items(data: Dictionary) -> Array:
	var value: Variant = data.get("items", [])
	return value as Array if value is Array else []

func _ok(code: int) -> bool: return code >= 200 and code < 300
func _message(code: int, data: Dictionary) -> String:
	var error: Variant = data.get("error", {})
	if error is Dictionary:
		var error_data := error as Dictionary
		var error_message := str(error_data.get("message", ""))
		var error_code := str(error_data.get("code", ""))
		if not error_message.is_empty():
			return "%s (%s)" % [error_message, error_code] if not error_code.is_empty() else error_message
	return str(data.get("detail", data.get("message", "HTTP %d" % code)))
func _url(path: String) -> String: return server_base_url.trim_suffix("/") + path

func _on_health(_result: int, code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	var data := _decode(body); health_received.emit(_ok(code), data, "" if _ok(code) else _message(code, data))
func _on_templates(_r: int, code: int, _h: PackedStringArray, body: PackedByteArray) -> void:
	var data := _decode(body); templates_received.emit(_ok(code), _items(data), "" if _ok(code) else _message(code, data))
func _on_quests(_r: int, code: int, _h: PackedStringArray, body: PackedByteArray) -> void:
	var data := _decode(body); quests_received.emit(_ok(code), _items(data), "" if _ok(code) else _message(code, data))
func _on_quest_history(_r: int, code: int, _h: PackedStringArray, body: PackedByteArray, request_context: Dictionary) -> void:
	var data := _decode(body)
	var success := _ok(code)
	var items := _items(data)
	var message := "" if success else _message(code, data)
	var total := maxi(0, int(data.get("total", items.size()))) if success else 0
	quest_history_received.emit(success, items, total, message, request_context)
	if not success:
		quest_history_error.emit(message, request_context)
func _on_failure(_r: int, code: int, _h: PackedStringArray, body: PackedByteArray, source_quest_id: String) -> void:
	var data := _decode(body)
	var success := _ok(code)
	var message := "" if success else _message(code, data)
	quest_failure_received.emit(success, data, message, source_quest_id)
	if not success:
		quest_failure_error.emit(message, source_quest_id)
func _on_create(_r: int, code: int, _h: PackedStringArray, body: PackedByteArray) -> void:
	var data := _decode(body); quest_created.emit(_ok(code), data.get("quest", data), "" if _ok(code) else _message(code, data))
func _on_confirm(_r: int, code: int, _h: PackedStringArray, body: PackedByteArray, source_quest_id: String) -> void:
	var data := _decode(body); quest_confirmed.emit(_ok(code), data.get("quest", data), "" if _ok(code) else _message(code, data), source_quest_id)
func _on_run(_r: int, code: int, _h: PackedStringArray, body: PackedByteArray, source_quest_id: String) -> void:
	var data := _decode(body)
	quest_started.emit(_ok(code), data, "" if _ok(code) else _message(code, data), source_quest_id)
func _on_control(_r: int, code: int, _h: PackedStringArray, body: PackedByteArray, _action: String, source_quest_id: String) -> void:
	var data := _decode(body)
	quest_controlled.emit(_ok(code), data, "" if _ok(code) else _message(code, data), source_quest_id)
func _on_decision(_r: int, code: int, _h: PackedStringArray, body: PackedByteArray, source_quest_id: String) -> void:
	var data := _decode(body)
	decision_submitted.emit(_ok(code), data, "" if _ok(code) else _message(code, data), source_quest_id)
func _on_quest(_r: int, code: int, _h: PackedStringArray, body: PackedByteArray, source_quest_id: String) -> void:
	var data := _decode(body); quest_received.emit(_ok(code), data.get("quest", data), "" if _ok(code) else _message(code, data), source_quest_id)
func _on_events(_r: int, code: int, _h: PackedStringArray, body: PackedByteArray, source_quest_id: String) -> void:
	var data := _decode(body)
	var items := _items(data)
	if _ok(code) and cursor_quest_id == source_quest_id:
		for item in items:
			if item is Dictionary:
				last_sequence = maxi(last_sequence, int(item.get("sequence", 0)))
	events_received.emit(_ok(code), items, "" if _ok(code) else _message(code, data), source_quest_id)
func _on_evidence(_r: int, code: int, _h: PackedStringArray, body: PackedByteArray, source_quest_id: String) -> void:
	var data := _decode(body); evidence_received.emit(_ok(code), _items(data), "" if _ok(code) else _message(code, data), source_quest_id)
func _on_artifacts(_r: int, code: int, _h: PackedStringArray, body: PackedByteArray, source_quest_id: String) -> void:
	var data := _decode(body); artifacts_received.emit(_ok(code), data, "" if _ok(code) else _message(code, data), source_quest_id)
func _on_artifact_preview(_r: int, code: int, _h: PackedStringArray, body: PackedByteArray, source_quest_id: String) -> void:
	var data := _decode(body); artifact_preview_received.emit(_ok(code), data, "" if _ok(code) else _message(code, data), source_quest_id)
func _on_artifact_review(_r: int, code: int, _h: PackedStringArray, body: PackedByteArray, source_quest_id: String) -> void:
	var data := _decode(body); artifact_reviewed.emit(_ok(code), data, "" if _ok(code) else _message(code, data), source_quest_id)
func _on_settings_received(_r: int, code: int, _h: PackedStringArray, body: PackedByteArray, request_context: Dictionary) -> void:
	var data := _decode(body); settings_received.emit(_ok(code), data, "" if _ok(code) else _message(code, data), request_context)
func _on_settings_saved(_r: int, code: int, _h: PackedStringArray, body: PackedByteArray, request_context: Dictionary) -> void:
	var data := _decode(body); settings_saved.emit(_ok(code), data, "" if _ok(code) else _message(code, data), request_context)
