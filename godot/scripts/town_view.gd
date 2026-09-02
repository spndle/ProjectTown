extends Control
class_name ProjectTownView

## Responsive side-view town with time-driven skies, parallax clouds, and
## softly covered entity silhouettes. Gameplay and text stay pixel-crisp.

const DAWN_BACKDROP: Texture2D = preload("res://assets/pixel_town/backgrounds/dawn-town-backdrop-v3.png")
const NOON_BACKDROP: Texture2D = preload("res://assets/pixel_town/backgrounds/noon-town-backdrop-v3.png")
const SUNSET_BACKDROP: Texture2D = preload("res://assets/pixel_town/backgrounds/sunset-town-backdrop-v2.png")
const NIGHT_BACKDROP: Texture2D = preload("res://assets/pixel_town/backgrounds/night-town-backdrop-v3.png")
const CLOUD_FAR: Texture2D = preload("res://assets/pixel_town/clouds/cloud-far-v1.png")
const CLOUD_MID: Texture2D = preload("res://assets/pixel_town/clouds/cloud-mid-v1.png")
const CLOUD_NEAR: Texture2D = preload("res://assets/pixel_town/clouds/cloud-near-v1.png")
const TOWN_DRAW_LAYER_SCRIPT := preload("res://scripts/town_draw_layer.gd")

const CLOUD_TRANSPARENT_EDGE_PX := 32.0

const SKY_ANCHORS := [330.0, 720.0, 1080.0, 1320.0, 1770.0]
const SKY_TEXTURES := [DAWN_BACKDROP, NOON_BACKDROP, SUNSET_BACKDROP, NIGHT_BACKDROP, DAWN_BACKDROP]

var quest_status := "idle"
var progress := 0.0
var quest_title := "等待新的 Quest"
var _npc_ratio := Vector2(0.50, 0.77)
var _target_ratio := Vector2(0.50, 0.77)
var _animation_time := 0.0
var _display_time_override_minutes := -1.0
var _entity_base_layer: ProjectTownDrawLayer
var _entity_shell_layer: ProjectTownDrawLayer
var _overlay_layer: ProjectTownDrawLayer

const BASE_EDGE_CLEANUP_SHADER := """
shader_type canvas_item;

bool is_magenta_fringe(vec4 color) {
	return color.a > 0.98 && color.r > 0.34 && color.b > 0.34
		&& color.g < min(color.r, color.b) * 0.56;
}

void fragment() {
	vec4 color = texture(TEXTURE, UV);
	vec2 pixel = TEXTURE_PIXEL_SIZE;
	bool silhouette_edge = texture(TEXTURE, UV + vec2(pixel.x, 0.0)).a < 0.98
		|| texture(TEXTURE, UV - vec2(pixel.x, 0.0)).a < 0.98
		|| texture(TEXTURE, UV + vec2(0.0, pixel.y)).a < 0.98
		|| texture(TEXTURE, UV - vec2(0.0, pixel.y)).a < 0.98;
	// Generated source art contains a one-pixel magenta matte around several
	// silhouettes. Recolour only matte pixels that touch transparent/outline
	// gutter; intentional purple pixels inside roofs, flowers and lamps remain.
	if (is_magenta_fringe(color) && silhouette_edge) {
		color = vec4(0.035, 0.055, 0.11, 1.0);
	}
	COLOR = color;
}
"""

const SOFT_SHELL_SHADER := """
shader_type canvas_item;

void fragment() {
	vec4 color = texture(TEXTURE, UV);
	// Original pixels are fully opaque. The padded gutter is deliberately
	// semi-opaque, so alpha cleanly selects the outline without sampling any
	// coloured source pixels into the LINEAR pass.
	if (color.a < 0.90 || color.a > 0.98) {
		discard;
	}
	COLOR = vec4(0.035, 0.055, 0.11, color.a);
}
"""


func _ready() -> void:
	texture_filter = CanvasItem.TEXTURE_FILTER_NEAREST
	_create_draw_layers()
	set_process(true)
	queue_redraw()


func set_quest_state(status: String, value: float, title: String = "") -> void:
	quest_status = status.to_lower()
	progress = clampf(value, 0.0, 1.0)
	if not title.is_empty():
		quest_title = title
	_target_ratio = _target_for_status(quest_status)
	queue_redraw()


func set_display_time_minutes(minutes: float) -> void:
	## Deterministic hook for preview/tests. Pass a negative value to resume system time.
	_display_time_override_minutes = minutes
	queue_redraw()


func get_display_period() -> String:
	var minutes := _display_minutes()
	if minutes >= 330.0 and minutes < 720.0:
		return "dawn"
	if minutes >= 720.0 and minutes < 1080.0:
		return "noon"
	if minutes >= 1080.0 and minutes < 1320.0:
		return "sunset"
	return "night"


func _target_for_status(status: String) -> Vector2:
	match status:
		"planned", "draft": return Vector2(0.20, 0.78)
		"running", "verifying", "replanning", "recovering": return Vector2(0.50, 0.78)
		"completed": return Vector2(0.80, 0.78)
		"waiting_user": return Vector2(0.68, 0.78)
		_: return Vector2(0.50, 0.78)


func _process(delta: float) -> void:
	_animation_time += delta
	_npc_ratio = _npc_ratio.lerp(_target_ratio, 1.0 - exp(-delta * 4.5))
	# Clouds and clock-driven crossfades need continuous redraw in every quest state.
	_sync_draw_layers()
	queue_redraw()


func _draw() -> void:
	var viewport_size := size
	_draw_time_backdrop(viewport_size)
	_draw_cloud_layers(viewport_size)


func _create_draw_layers() -> void:
	_entity_base_layer = _make_draw_layer(ProjectTownDrawLayer.MODE_ENTITIES, CanvasItem.TEXTURE_FILTER_NEAREST)
	var cleanup_material := ShaderMaterial.new()
	var cleanup_shader := Shader.new()
	cleanup_shader.code = BASE_EDGE_CLEANUP_SHADER
	cleanup_material.shader = cleanup_shader
	_entity_base_layer.material = cleanup_material
	_entity_shell_layer = _make_draw_layer(ProjectTownDrawLayer.MODE_SHELL, CanvasItem.TEXTURE_FILTER_LINEAR)
	var shell_material := ShaderMaterial.new()
	var shell_shader := Shader.new()
	shell_shader.code = SOFT_SHELL_SHADER
	shell_material.shader = shell_shader
	_entity_shell_layer.material = shell_material
	_overlay_layer = _make_draw_layer(ProjectTownDrawLayer.MODE_OVERLAY, CanvasItem.TEXTURE_FILTER_NEAREST)
	_sync_draw_layers()


func _make_draw_layer(mode: int, filter_mode: CanvasItem.TextureFilter) -> ProjectTownDrawLayer:
	var layer: ProjectTownDrawLayer = TOWN_DRAW_LAYER_SCRIPT.new()
	layer.draw_mode = mode
	layer.mouse_filter = Control.MOUSE_FILTER_IGNORE
	layer.texture_filter = filter_mode
	layer.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	add_child(layer)
	return layer


func _sync_draw_layers() -> void:
	if not is_instance_valid(_entity_base_layer):
		return
	for layer in [_entity_base_layer, _entity_shell_layer, _overlay_layer]:
		layer.configure(quest_status, progress, quest_title, _npc_ratio, _animation_time)


func _display_minutes() -> float:
	if _display_time_override_minutes >= 0.0:
		return fposmod(_display_time_override_minutes, 1440.0)
	var clock := Time.get_time_dict_from_system()
	return float(int(clock.get("hour", 12)) * 60 + int(clock.get("minute", 0)))


func _sky_blend() -> Dictionary:
	var minutes := _display_minutes()
	if minutes < SKY_ANCHORS[0]:
		minutes += 1440.0
	for index in range(SKY_ANCHORS.size() - 1):
		var start: float = SKY_ANCHORS[index]
		var finish: float = SKY_ANCHORS[index + 1]
		if minutes >= start and minutes <= finish:
			var ratio := clampf((minutes - start) / (finish - start), 0.0, 1.0)
			var eased := smoothstep(0.0, 1.0, ratio)
			return {"from": SKY_TEXTURES[index], "to": SKY_TEXTURES[index + 1], "weight": eased}
	return {"from": NIGHT_BACKDROP, "to": DAWN_BACKDROP, "weight": 0.0}


func _draw_time_backdrop(viewport_size: Vector2) -> void:
	var blend := _sky_blend()
	# Draw the base fully opaque, then alpha-over the next period. Fading both
	# layers would darken the midpoint because normal alpha-over is not additive.
	_draw_cover_texture(blend.from as Texture2D, viewport_size, Color.WHITE)
	_draw_cover_texture(blend.to as Texture2D, viewport_size, Color(1, 1, 1, float(blend.weight)))


func _draw_cover_texture(texture: Texture2D, viewport_size: Vector2, tint: Color) -> void:
	var texture_size := texture.get_size()
	var scale_factor := maxf(viewport_size.x / texture_size.x, viewport_size.y / texture_size.y)
	var source_size := viewport_size / scale_factor
	var source_origin := (texture_size - source_size) * 0.5
	draw_texture_rect_region(texture, Rect2(Vector2.ZERO, viewport_size), Rect2(source_origin, source_size), tint)


func _draw_cloud_layers(viewport_size: Vector2) -> void:
	var night_factor := _night_factor()
	_draw_scrolling_clouds(CLOUD_FAR, viewport_size, 5.0, 0.46, viewport_size.y * 0.08, Color(0.62, 0.72, 0.88, lerpf(0.20, 0.10, night_factor)))
	_draw_scrolling_clouds(CLOUD_MID, viewport_size, 12.0, 0.58, viewport_size.y * 0.16, Color(0.80, 0.86, 0.96, lerpf(0.28, 0.14, night_factor)))
	_draw_scrolling_clouds(CLOUD_NEAR, viewport_size, 24.0, 0.64, viewport_size.y * 0.26, Color(1.0, 1.0, 1.0, lerpf(0.34, 0.17, night_factor)))


func _draw_scrolling_clouds(texture: Texture2D, viewport_size: Vector2, speed: float, scale_factor: float, y: float, tint: Color) -> void:
	var scaled_size := texture.get_size() * scale_factor
	# Every strip has a 32px transparent fade on each side. Advance by the
	# non-faded span so neighbouring strips overlap through both fades.
	var tile_step := scaled_size.x - CLOUD_TRANSPARENT_EDGE_PX * 2.0 * scale_factor
	var offset := fposmod(_animation_time * speed, tile_step)
	var x := -offset - tile_step
	while x < viewport_size.x:
		draw_texture_rect(texture, Rect2(Vector2(round(x), round(y)), scaled_size), false, tint)
		x += tile_step


func _night_factor() -> float:
	var blend := _sky_blend()
	if blend.to == NIGHT_BACKDROP:
		return float(blend.weight)
	if blend.from == NIGHT_BACKDROP:
		return 1.0 - float(blend.weight)
	return 0.0
