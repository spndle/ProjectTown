extends SceneTree

const TOWN_SCRIPT: Script = preload("res://scripts/town_view.gd")


func _initialize() -> void:
	var town: ProjectTownView = TOWN_SCRIPT.new()
	root.add_child(town)
	var period_cases := [
		[0.0, "night"],
		[330.0, "dawn"],
		[720.0, "noon"],
		[1080.0, "sunset"],
		[1320.0, "night"],
		[1439.0, "night"],
	]
	for item in period_cases:
		town.set_display_time_minutes(float(item[0]))
		if town.get_display_period() != str(item[1]):
			_fail("period mismatch at minute %s" % item[0])
			return

	# Every transition midpoint uses smoothstep(0, 1, 0.5) == 0.5.
	town.set_display_time_minutes(525.0)
	var blend: Dictionary = town.call("_sky_blend")
	if not is_equal_approx(float(blend.weight), 0.5):
		_fail("dawn/noon midpoint is not a half blend")
		return

	# Cloud dimming follows the exact same blend, including across midnight.
	for minute in [1200.0, 1545.0]:
		town.set_display_time_minutes(minute)
		if not is_equal_approx(float(town.call("_night_factor")), 0.5):
			_fail("night cloud factor is discontinuous at minute %s" % minute)
			return

	print("TIME_CYCLE_SMOKE_OK anchors=6 midpoints=3")
	town.queue_free()
	quit(0)


func _fail(message: String) -> void:
	printerr("TIME_CYCLE_SMOKE_FAILED: %s" % message)
	quit(1)
