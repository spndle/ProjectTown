extends SceneTree

## Offline-only deterministic visual-capture base.  Fixture scripts override
## configure_fixture() with fixed, synthetic UI state; they never create API
## clients or issue transport requests.
const MAIN_SCENE := preload("res://main.tscn")
const SETTLE_FRAMES := 12

var fixture_scene: Control
var frames_remaining := SETTLE_FRAMES
var prepared := false


func fixture_id() -> String:
	return "base"


func _initialize() -> void:
	fixture_scene = MAIN_SCENE.instantiate()
	# This must precede add_child(): Main._ready() uses it to avoid API setup.
	fixture_scene.transport_enabled = false
	root.add_child(fixture_scene)
	call_deferred("_prepare_capture")


func _prepare_capture() -> void:
	await process_frame
	configure_fixture(fixture_scene)
	await process_frame
	settle_fixture(fixture_scene)
	_lock_deterministic_visuals()
	await process_frame
	_assert_offline()
	prepared = true


func configure_fixture(_scene: Control) -> void:
	pass


func settle_fixture(_scene: Control) -> void:
	pass


func _lock_deterministic_visuals() -> void:
	# TownView normally reads system time and advances cloud/NPC animation. Lock
	# both after each synthetic state has been applied so PNG bytes are repeatable.
	var town_view: Node = fixture_scene.get("town_view") as Node
	if town_view == null:
		_fail("offline fixture did not create TownView")
		return
	town_view.call("set_display_time_minutes", 720.0) # fixed noon/daylight backdrop
	town_view.set("_animation_time", 0.0)
	town_view.set("_npc_ratio", town_view.call("_target_for_status", fixture_scene.active_status))
	town_view.call("_sync_draw_layers")
	town_view.set_process(false)


func _process(_delta: float) -> bool:
	if not prepared:
		return false
	frames_remaining -= 1
	if frames_remaining <= 0:
		_capture()
	return false


func _assert_offline() -> void:
	if fixture_scene.api != null:
		_fail("api must remain null in offline fixture")
		return
	if fixture_scene.poll_timer == null:
		_fail("offline fixture must install a stopped poll timer")
		return
	if not fixture_scene.poll_timer.is_stopped():
		_fail("poll timer must not run in offline fixture")


func _capture() -> void:
	var output := OS.get_environment("PROJECTTOWN_VISUAL_OUTPUT")
	if output.is_empty():
		_fail("PROJECTTOWN_VISUAL_OUTPUT is required")
		return
	var sandbox_root := ProjectSettings.globalize_path("res://").path_join("../sandbox").simplify_path().replace("\\", "/").to_lower()
	var normalized_output := output.simplify_path().replace("\\", "/").to_lower()
	if not output.is_absolute_path() or not normalized_output.begins_with(sandbox_root + "/"):
		_fail("PROJECTTOWN_VISUAL_OUTPUT must be an absolute path inside sandbox")
		return
	var output_dir := output.get_base_dir()
	if output_dir.is_empty() or DirAccess.make_dir_recursive_absolute(output_dir) != OK:
		_fail("unable to create capture output directory")
		return
	var texture := root.get_texture()
	if texture == null:
		_fail("headless renderer did not provide a readable viewport texture")
		return
	var image := texture.get_image()
	if image == null:
		_fail("headless renderer returned no viewport image")
		return
	var window_size := DisplayServer.window_get_size()
	# At non-16:9 window sizes, canvas_items keeps the project's 16:9 render
	# area and the physical window gains letterbox bars. Compose those fixed
	# black bars into the candidate so its PNG dimensions equal --resolution.
	if image.get_width() != window_size.x or image.get_height() != window_size.y:
		if image.get_width() > window_size.x or image.get_height() > window_size.y:
			_fail("render image exceeds requested window: image=%dx%d window=%dx%d" % [image.get_width(), image.get_height(), window_size.x, window_size.y])
			return
		var window_image := Image.create(window_size.x, window_size.y, false, Image.FORMAT_RGBA8)
		window_image.fill(Color.BLACK)
		var origin := Vector2i((window_size.x - image.get_width()) / 2, (window_size.y - image.get_height()) / 2)
		window_image.blit_rect(image, Rect2i(0, 0, image.get_width(), image.get_height()), origin)
		image = window_image
	var result := image.save_png(output)
	if result != OK:
		_fail("failed to save PNG: %s" % error_string(result))
		return
	print("PROJECTTOWN_CAPTURE_OK fixture=%s path=%s size=%dx%d offline=true" % [fixture_id(), output, image.get_width(), image.get_height()])
	quit(0)


func _fail(message: String) -> void:
	printerr("PROJECTTOWN_CAPTURE_FAILED fixture=%s reason=%s" % [fixture_id(), message])
	quit(1)
