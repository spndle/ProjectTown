extends Control
class_name ProjectTownDrawLayer

## Draws one visual stratum of the town.  The base stratum is deliberately
## nearest-filtered; the shell stratum is a separate linear-filtered coverage
## pass that contains only the translucent two-pixel gutter around sprites.

const BUILDING_ATLAS: Texture2D = preload("res://assets/pixel_town/buildings/guild-buildings-atlas-outline-v4.png")
const COURIER_ATLAS: Texture2D = preload("res://assets/pixel_town/characters/guild-courier-sheet-outline-v4.png")
const ENVIRONMENT_ATLAS: Texture2D = preload("res://assets/pixel_town/environment/town-props-atlas-outline-v4.png")
const PIXEL_FONT: FontFile = preload("res://assets/fonts/fusion-pixel-12px-proportional-zh-hans.ttf")

const MODE_ENTITIES := 0
const MODE_SHELL := 1
const MODE_OVERLAY := 2

const PIXEL := 4.0
const INK := Color("#11182f")
const PARCHMENT_LIGHT := Color("#fff2c4")
const BUILDING_SOURCE_CELL := Vector2(181, 181)
const BUILDING_PADDED_CELL := Vector2(185, 185)
const COURIER_SOURCE_CELL := Vector2(88, 140)
const COURIER_PADDED_CELL := Vector2(92, 144)
const BUILDING_OPAQUE_BASELINE := 162.0
const TREE_OPAQUE_BASELINE := 126.0
const PROP_OPAQUE_BASELINE := 68.0
const COURIER_OPAQUE_BASELINES := [125.0, 125.0, 125.0, 125.0, 106.0, 106.0, 106.0, 106.0]
# The terrain art has transparent end caps inside its 350px source cell. Crop
# to the continuous inner span so repeated tiles do not leave floating gaps or
# expose unrelated atlas fragments below the grass line.
const GROUND_SOURCE := Rect2(42, 205, 261, 40)

var draw_mode := MODE_ENTITIES
var quest_status := "idle"
var progress := 0.0
var quest_title := "等待新的 Quest"
var npc_ratio := Vector2(0.50, 0.77)
var animation_time := 0.0


func configure(status: String, value: float, title: String, ratio: Vector2, time_seconds: float) -> void:
	quest_status = status
	progress = value
	quest_title = title
	npc_ratio = ratio
	animation_time = time_seconds
	queue_redraw()


func _draw() -> void:
	match draw_mode:
		MODE_ENTITIES, MODE_SHELL:
			_draw_entities(draw_mode == MODE_SHELL)
		MODE_OVERLAY:
			_draw_overlay()


func _scene_scale() -> float:
	return clampf(minf(size.x / 720.0, size.y / 650.0), 0.68, 1.0)


func _draw_entities(shell_only: bool) -> void:
	var scene_scale := _scene_scale()
	var ground_y := size.y * 0.76
	_draw_environment(shell_only, scene_scale, ground_y)
	_draw_buildings(shell_only, scene_scale, ground_y)
	_draw_courier(shell_only, scene_scale, ground_y)


func _draw_environment(shell_only: bool, scene_scale: float, ground_y: float) -> void:
	_draw_environment_region(Rect2(0, 0, 130, 130), _baseline_destination(Vector2(4, ground_y), Vector2(130, 130), TREE_OPAQUE_BASELINE, scene_scale), shell_only)
	_draw_environment_region(Rect2(250, 0, 100, 130), _baseline_destination(Vector2(size.x - 105 * scene_scale, ground_y), Vector2(100, 130), TREE_OPAQUE_BASELINE, scene_scale), shell_only)
	var ground_size := GROUND_SOURCE.size * scene_scale
	var tile_x := 0.0
	while tile_x < size.x:
		if not shell_only:
			draw_texture_rect_region(ENVIRONMENT_ATLAS, Rect2(Vector2(tile_x, ground_y), ground_size), Rect2(GROUND_SOURCE.position + Vector2(2, 2), GROUND_SOURCE.size))
		tile_x += ground_size.x
	_draw_environment_region(Rect2(166, 132, 88, 72), _baseline_destination(Vector2(size.x * 0.31 - 44 * scene_scale, ground_y), Vector2(88, 72), PROP_OPAQUE_BASELINE, scene_scale), shell_only)
	_draw_environment_region(Rect2(166, 132, 88, 72), _baseline_destination(Vector2(size.x * 0.69 - 44 * scene_scale, ground_y), Vector2(88, 72), PROP_OPAQUE_BASELINE, scene_scale), shell_only)
	_draw_environment_region(Rect2(254, 132, 96, 72), _baseline_destination(Vector2(size.x * 0.91 - 48 * scene_scale, ground_y), Vector2(96, 72), PROP_OPAQUE_BASELINE, scene_scale), shell_only)


func _draw_environment_region(source: Rect2, destination: Rect2, shell_only: bool) -> void:
	if shell_only:
		_draw_shell_region(ENVIRONMENT_ATLAS, Rect2(source.position, source.size + Vector2(4, 4)), destination)
	else:
		draw_texture_rect_region(ENVIRONMENT_ATLAS, destination, Rect2(source.position + Vector2(2, 2), source.size))


func _draw_buildings(shell_only: bool, scene_scale: float, ground_y: float) -> void:
	var building_top := ground_y - BUILDING_OPAQUE_BASELINE * scene_scale
	for index in range(3):
		var center_top := Vector2(size.x * [0.20, 0.50, 0.80][index], building_top)
		var display_size := BUILDING_SOURCE_CELL * scene_scale
		var destination := Rect2(center_top - Vector2(display_size.x * 0.5, 0), display_size)
		if shell_only:
			_draw_shell_region(BUILDING_ATLAS, Rect2(Vector2(index * BUILDING_PADDED_CELL.x, 0), BUILDING_PADDED_CELL), destination)
		else:
			var source := Rect2(Vector2(index * BUILDING_PADDED_CELL.x + 2, 2), BUILDING_SOURCE_CELL)
			draw_texture_rect_region(BUILDING_ATLAS, destination, source)


func _draw_courier(shell_only: bool, scene_scale: float, ground_y: float) -> void:
	var frame := _courier_frame()
	var column := frame % 4
	var row := frame / 4
	var bob := sin(animation_time * 8.0) * 2.0 if quest_status in ["running", "replanning", "recovering"] else 0.0
	var display_size := COURIER_SOURCE_CELL * scene_scale
	var destination := _baseline_destination(Vector2(npc_ratio.x * size.x, ground_y), COURIER_SOURCE_CELL, COURIER_OPAQUE_BASELINES[frame], scene_scale)
	destination.position.x -= display_size.x * 0.5
	destination.position.y += bob
	if shell_only:
		_draw_shell_region(COURIER_ATLAS, Rect2(Vector2(column * COURIER_PADDED_CELL.x, row * COURIER_PADDED_CELL.y), COURIER_PADDED_CELL), destination)
	else:
		var source := Rect2(Vector2(column * COURIER_PADDED_CELL.x + 2, row * COURIER_PADDED_CELL.y + 2), COURIER_SOURCE_CELL)
		draw_texture_rect_region(COURIER_ATLAS, destination, source)


func _baseline_destination(anchor: Vector2, source_size: Vector2, opaque_baseline: float, scene_scale: float) -> Rect2:
	var display_size := source_size * scene_scale
	# The opaque baseline is the inclusive row index of the last source pixel.
	# Align that row's lower edge to ground_y, not its upper edge.
	return Rect2(Vector2(anchor.x, anchor.y - (opaque_baseline + 1.0) * scene_scale), display_size)


func _draw_shell_region(texture: Texture2D, padded_source: Rect2, destination: Rect2) -> void:
	# The first gutter pixel carries a 1px near-black outline; the outer gutter
	# stays transparent. This pass is linear-filtered while the base remains crisp.
	var shell_padding := Vector2(2, 2) * (destination.size / (padded_source.size - Vector2(4, 4)))
	draw_texture_rect_region(texture, destination.grow_individual(shell_padding.x, shell_padding.y, shell_padding.x, shell_padding.y), padded_source)


func _draw_overlay() -> void:
	_draw_building_labels()
	_draw_status_bubble()
	_draw_map_header()
	_draw_quest_hud()


func _draw_building_labels() -> void:
	var scene_scale := _scene_scale()
	var ground_y := size.y * 0.76
	var names := ["任务大厅", "执行工坊", "成果验收所"]
	var roles := ["规划与立项", "执行与验证", "预览与确认"]
	var compact := size.x < 470.0
	var panel_height := 25.0 if compact else 34.0
	var panel_width := 96.0 if compact else 116.0
	# Treat each label as a sign inset into the terrain fascia. It no longer
	# covers a building facade, the courier's feet, or the grass contact line.
	var panel_y := ground_y + 4.0 * scene_scale
	for index in range(3):
		var panel := Rect2(size.x * [0.20, 0.50, 0.80][index] - panel_width * 0.5, panel_y, panel_width, panel_height)
		_draw_building_label(panel, names[index], roles[index], compact)


func _courier_frame() -> int:
	var phase := int(floor(animation_time * 3.5)) % 2
	match quest_status:
		"planned", "draft": return 4
		"running", "replanning", "recovering": return 2 + phase
		"verifying": return 5
		"waiting_user", "paused", "failed", "budget_exhausted": return 6
		"completed": return 7
		_: return phase


func _draw_building_label(panel: Rect2, name: String, role: String, compact: bool) -> void:
	_draw_pixel_panel(panel, Color("#3a2d3d"), Color("#8f552d"))
	if compact:
		_draw_centered_text(_fit_text(name, panel.size.x - 12.0, 13), panel.position + Vector2(panel.size.x * 0.5, 17), 13, PARCHMENT_LIGHT)
		return
	_draw_centered_text(_fit_text(name, panel.size.x - 12.0, 14), panel.position + Vector2(panel.size.x * 0.5, 16), 14, PARCHMENT_LIGHT)
	_draw_centered_text(_fit_text(role, panel.size.x - 12.0, 11), panel.position + Vector2(panel.size.x * 0.5, 28), 11, Color("#d5e7cf"))


func _draw_status_bubble() -> void:
	var scene_scale := _scene_scale()
	var ground_y := size.y * 0.76
	var frame := _courier_frame()
	var bob := sin(animation_time * 8.0) * 2.0 if quest_status in ["running", "replanning", "recovering"] else 0.0
	var destination := _baseline_destination(Vector2(npc_ratio.x * size.x, ground_y), COURIER_SOURCE_CELL, COURIER_OPAQUE_BASELINES[frame], scene_scale)
	destination.position.x -= destination.size.x * 0.5
	destination.position.y += bob
	var state_text := _fit_text(_status_text(), maxf(112.0, size.x * 0.28), 12)
	var bubble_size := PIXEL_FONT.get_string_size(state_text, HORIZONTAL_ALIGNMENT_LEFT, -1, 12) + Vector2(30, 19)
	var bubble := Rect2(clampf(npc_ratio.x * size.x - bubble_size.x * 0.5, 6.0, maxf(6.0, size.x - bubble_size.x - 6.0)), destination.position.y - bubble_size.y - 11.0, bubble_size.x, bubble_size.y)
	_draw_pixel_panel(bubble, Color("#fff2c4"), Color("#8f552d"))
	var tail_x := clampf(npc_ratio.x * size.x, bubble.position.x + 12.0, bubble.end.x - 12.0)
	var tail_outline := PackedVector2Array([Vector2(tail_x - 6, bubble.end.y - 1), Vector2(tail_x + 6, bubble.end.y - 1), Vector2(tail_x, bubble.end.y + 7)])
	draw_colored_polygon(tail_outline, INK)
	var tail_fill := PackedVector2Array([Vector2(tail_x - 3, bubble.end.y - 1), Vector2(tail_x + 3, bubble.end.y - 1), Vector2(tail_x, bubble.end.y + 3)])
	draw_colored_polygon(tail_fill, Color("#fff2c4"))
	draw_rect(Rect2(bubble.position + Vector2(8, 8), Vector2(7, 7)), _status_color(), true)
	draw_string(PIXEL_FONT, bubble.position + Vector2(20, bubble.size.y - 7), state_text, HORIZONTAL_ALIGNMENT_LEFT, -1, 12, INK)


func _draw_map_header() -> void:
	var compact := size.x < 470.0
	var panel_width := minf(size.x - 32.0, 346.0)
	var panel_height := 46.0 if compact else 70.0
	var panel := Rect2(20, 18, panel_width, panel_height)
	_draw_pixel_panel(panel, Color("#25365c"), Color("#604a58"))
	draw_rect(Rect2(panel.position + Vector2(10, 11), Vector2(8, 22)), Color("#d6b258"), true)
	_draw_text(_fit_text("PROJECTTOWN", panel_width - 42, 24), panel.position + Vector2(28, 34), 24, PARCHMENT_LIGHT)
	if not compact:
		_draw_text(_fit_text("把个人目标变成可追踪的 Agent Quest", panel_width - 28, 12), panel.position + Vector2(14, 56), 12, Color("#b8d4d1"))


func _draw_quest_hud() -> void:
	var compact := size.x < 470.0
	var hud_width := minf(500.0 if compact else 580.0, size.x - 40.0)
	var hud_height := 64.0 if compact else 68.0
	var hud := Rect2(20, size.y - hud_height - 20.0, hud_width, hud_height)
	_draw_pixel_panel(hud, Color("#25365c"), Color("#604a58"))
	var tag := Rect2(hud.position + Vector2(10, 10), Vector2(58, 16))
	_draw_pixel_panel(tag, _status_color().darkened(0.35), Color("#8f552d"), 1.0)
	_draw_centered_text(_fit_text(_status_short_text(), tag.size.x - 6.0, 10), tag.get_center() + Vector2(0, 4), 10, Color.WHITE)
	var percentage := "%d%%" % roundi(progress * 100.0)
	_draw_text(percentage, Vector2(hud.end.x - 43, hud.position.y + 23), 12, PARCHMENT_LIGHT)
	_draw_text(_fit_text(quest_title, hud.size.x - 92.0, 12), hud.position + Vector2(78, 22), 12, PARCHMENT_LIGHT)
	var bar := Rect2(hud.position + Vector2(11, 40), Vector2(hud.size.x - 22, 13))
	_draw_pixel_panel(bar, Color("#17233c"), Color("#604a58"), 1.0)
	var fill := Rect2(bar.position + Vector2(3, 3), Vector2(maxf(0.0, (bar.size.x - 6.0) * progress), bar.size.y - 6.0))
	draw_rect(fill, _status_color(), true)


func _draw_pixel_panel(panel: Rect2, fill: Color, shadow: Color, border_width: float = 2.0) -> void:
	var inset := border_width
	var outer := panel.grow(inset)
	# Stepped corners keep every UI surface in the same pixel-language.
	draw_rect(Rect2(outer.position + Vector2(2, 2), outer.size), shadow, true)
	draw_colored_polygon(_chamfered_rect_points(outer, 2.0), INK)
	draw_colored_polygon(_chamfered_rect_points(panel, 2.0), fill)
	draw_rect(Rect2(panel.position + Vector2(3, 3), Vector2(panel.size.x - 6, 1)), Color("#ffffff55"), true)
	draw_rect(Rect2(panel.position + Vector2(3, 3), Vector2(1, panel.size.y - 6)), Color("#ffffff44"), true)


func _chamfered_rect_points(rect: Rect2, cut: float) -> PackedVector2Array:
	return PackedVector2Array([
		Vector2(rect.position.x + cut, rect.position.y), Vector2(rect.end.x - cut, rect.position.y),
		Vector2(rect.end.x, rect.position.y + cut), Vector2(rect.end.x, rect.end.y - cut),
		Vector2(rect.end.x - cut, rect.end.y), Vector2(rect.position.x + cut, rect.end.y),
		Vector2(rect.position.x, rect.end.y - cut), Vector2(rect.position.x, rect.position.y + cut),
	])


func _fit_text(text: String, max_width: float, font_size: int) -> String:
	if PIXEL_FONT.get_string_size(text, HORIZONTAL_ALIGNMENT_LEFT, -1, font_size).x <= max_width:
		return text
	var shortened := text
	while not shortened.is_empty():
		shortened = shortened.left(shortened.length() - 1)
		var candidate := shortened + "…"
		if PIXEL_FONT.get_string_size(candidate, HORIZONTAL_ALIGNMENT_LEFT, -1, font_size).x <= max_width:
			return candidate
	return "…"


func _draw_centered_text(text: String, position: Vector2, font_size: int, color: Color) -> void:
	var width := PIXEL_FONT.get_string_size(text, HORIZONTAL_ALIGNMENT_LEFT, -1, font_size).x
	draw_string(PIXEL_FONT, position - Vector2(width * 0.5, 0), text, HORIZONTAL_ALIGNMENT_LEFT, -1, font_size, color)


func _draw_text(text: String, position: Vector2, font_size: int, color: Color) -> void:
	draw_string(PIXEL_FONT, position, text, HORIZONTAL_ALIGNMENT_LEFT, -1, font_size, color)


func _status_color() -> Color:
	match quest_status:
		"planned", "draft": return Color("#5aa5db")
		"running", "verifying", "replanning", "recovering": return Color("#e4a34f")
		"completed": return Color("#60b779")
		"failed", "budget_exhausted": return Color("#d65c61")
		"waiting_user", "paused": return Color("#d6b258")
		_: return Color("#90a2ad")


func _status_short_text() -> String:
	match quest_status:
		"planned", "draft": return "计划"
		"running", "replanning", "recovering": return "执行"
		"verifying": return "验收"
		"completed": return "完成"
		"failed", "budget_exhausted": return "受阻"
		"waiting_user": return "待确认"
		"paused": return "暂停"
		_: return "待命"


func _status_text() -> String:
	match quest_status:
		"planned", "draft": return "在任务大厅整理计划"
		"running", "replanning", "recovering": return "在执行工坊工作中"
		"verifying": return "正在核验成果"
		"completed": return "成果已送到验收所"
		"failed", "budget_exhausted": return "任务受阻，等待检查"
		"waiting_user": return "需要你的确认"
		"paused": return "任务已暂停"
		_: return "等待新的 Quest"
