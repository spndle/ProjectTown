extends SceneTree

var frames_remaining := 16


func _initialize() -> void:
	var packed_scene: PackedScene = load("res://main.tscn")
	var scene: Node = packed_scene.instantiate()
	get_root().add_child(scene)
	scene.call_deferred("_show_tutorial")


func _process(_delta: float) -> bool:
	frames_remaining -= 1
	if frames_remaining == 0:
		_capture()
	return false


func _capture() -> void:
	var output := OS.get_environment("PROJECTTOWN_VISUAL_OUTPUT")
	if output.is_empty():
		printerr("PROJECTTOWN_VISUAL_OUTPUT is required")
		quit(1)
		return
	var image := get_root().get_texture().get_image()
	var result := image.save_png(output)
	if result != OK:
		printerr("Failed to save tutorial capture: %s" % error_string(result))
		quit(1)
		return
	print("GODOT_TUTORIAL_CAPTURE_OK path=%s size=%dx%d" % [output, image.get_width(), image.get_height()])
	quit(0)
