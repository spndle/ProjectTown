extends Control

const ProjectTownAPIClass := preload("res://scripts/api_client.gd")
const PIXEL_FONT := preload("res://assets/fonts/fusion-pixel-12px-proportional-zh-hans.ttf")
const GUILD_COURIER_PORTRAITS := preload("res://assets/pixel_town/portraits/guild-courier-portraits-v2.png")

@export var server_base_url := "http://127.0.0.1:8000"
@export var transport_enabled := true
@export_range(0.5, 10.0, 0.5) var poll_interval_seconds := 1.0
@export_range(1, 60, 1) var projection_poll_every := 5

@onready var town_view: ProjectTownView = $TownView

var api: ProjectTownAPI
var poll_timer: Timer
var active_quest_id := ""
var active_status := "idle"
var state_version := 1
var contract_version := 1
var budget_usage: Dictionary = {}
var templates: Array[Dictionary] = []
var last_trace_sequence := 0
var event_lines: PackedStringArray = []
var seen_event_sequences: Dictionary = {}
var last_evidence_count := 0
var artifact_items: Array[Dictionary] = []
var active_artifact_id := ""
var result_review_key := ""
var result_manifest_hash := ""
var artifact_review_pending := false
var final_projection_requested := false
var pending_action_id := ""
var last_suggested_goal := ""
var projection_poll_ticks := 0
var websocket_retry_seconds := 1.0
var next_websocket_retry_msec := 0

var backend_dot: Label
var backend_label: Label
var template_select: OptionButton
var goal_input: TextEdit
var workspace_input: LineEdit
var create_button: Button
var refresh_button: Button
var control_button: Button
var approve_button: Button
var modify_button: Button
var reject_button: Button
var quest_id_label: Label
var status_badge_panel: PanelContainer
var status_badge: Label
var current_step_label: Label
var progress_bar: ProgressBar
var contract_summary: RichTextLabel
var evidence_list: ItemList
var milestones_list: ItemList
var trace_log: RichTextLabel
var onboarding_panel: PanelContainer
var onboarding_portrait: TextureRect
var onboarding_step_label: Label
var onboarding_help_label: Label
var template_help_label: Label
var history_select: OptionButton
var restore_quest_button: Button
var history_status_label: Label
var history_query: LineEdit
var history_status_menu: MenuButton
var history_refresh_button: Button
var history_previous_button: Button
var history_next_button: Button
var history_page_label: Label
var failure_detail_label: RichTextLabel
var history_generation := 0
var history_offset := 0
var history_total := 0
const HISTORY_PAGE_SIZE := 20
const HISTORY_STATUSES := ["draft", "planned", "running", "verifying", "replanning", "waiting_user", "paused", "recovering", "discarding", "completed", "budget_exhausted", "failed"]
var tutorial_dialog: AcceptDialog
var settings_dialog: Window
var settings_button: Button
var settings_provider: OptionButton
var settings_provider_label: Label
var settings_status_label: Label
var settings_base_url: LineEdit
var settings_model: LineEdit
var settings_model_hint: Label
var settings_api_key: LineEdit
var settings_clear_key: CheckBox
var settings_save_button: Button
var settings_revision := ""
var settings_generation := 0
var settings_selected_provider := "openai"
var settings_fixture: Dictionary = {}
var result_panel: PanelContainer
var result_state_label: Label
var artifact_provenance_label: Label
var result_select: OptionButton
var result_preview: TextEdit
var artifact_preview_detail_button: Button
var artifact_preview_dialog: Window
var artifact_preview_dialog_text: TextEdit
var keep_result_button: Button
var discard_result_button: Button
var discard_confirmation: ConfirmationDialog
var layout_divider: ColorRect
var control_panel: PanelContainer
var control_margin: MarginContainer
var header_title: Label
var header_subtitle: Label
var tutorial_button: Button
var options_row: HBoxContainer
var workspace_column: VBoxContainer
var action_row: HBoxContainer
var decision_row: HBoxContainer
var endpoint_hint: Label
var _compact_layout := false
var _applied_responsive_width := -1
var active_preview_content := ""
var active_preview_path := ""
var active_preview_hash := ""
var preview_ready := false

## Original fantasy-sandbox pixel palette.  The UI deliberately avoids copied
## game assets while using crisp square frames, slate, wood and parchment.
const COLOR_BG := Color("#111a2d")
const COLOR_PANEL := Color("#202b42")
const COLOR_PIXEL_BLACK := Color("#0f1424")
const COLOR_INNER_LIGHT := Color("#d8c28d")
const COLOR_INK := Color("#f5e6bd")
const COLOR_MUTED := Color("#b8c4ca")
const COLOR_BORDER := Color("#8d6a3d")
const COLOR_PRIMARY := Color("#3d8fb5")
const COLOR_ACCENT := Color("#d79a48")
const COLOR_SUCCESS := Color("#5aa66f")
const COLOR_STONE := Color("#34435b")
const COLOR_DEEP_STONE := Color("#182238")
const COLOR_WOOD := Color("#6d4931")
const COLOR_PARCHMENT := Color("#f0dfae")
const COLOR_DANGER := Color("#bd5b55")


func _ready() -> void:
	_install_pixel_theme()
	_build_interface()
	resized.connect(_apply_responsive_layout)
	call_deferred("_apply_responsive_layout")
	_load_fallback_templates()
	if not transport_enabled:
		_install_poll_timer(false)
		_set_backend_state(false, "离线夹具")
		return
	_setup_api()
	_install_poll_timer()
	_set_backend_state(false, "连接中…")
	api.fetch_health()
	api.fetch_templates()
	api.fetch_quests()
	_request_history_page()


func _build_interface() -> void:
	var background := ColorRect.new()
	background.color = COLOR_BG
	background.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	background.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(background)
	move_child(background, 0)

	layout_divider = ColorRect.new()
	layout_divider.color = COLOR_PIXEL_BLACK
	layout_divider.set_anchors_preset(Control.PRESET_CENTER_RIGHT)
	layout_divider.anchor_left = 0.675
	layout_divider.anchor_right = 0.675
	layout_divider.anchor_top = 0.0
	layout_divider.anchor_bottom = 1.0
	layout_divider.offset_left = -2.0
	layout_divider.offset_right = 0.0
	add_child(layout_divider)

	control_panel = PanelContainer.new()
	control_panel.name = "ControlPanel"
	control_panel.set_anchors_preset(Control.PRESET_RIGHT_WIDE)
	control_panel.anchor_left = 0.675
	control_panel.offset_left = 0.0
	control_panel.add_theme_stylebox_override("panel", _pixel_frame(COLOR_PANEL, COLOR_PIXEL_BLACK, COLOR_BORDER, 2, 0))
	add_child(control_panel)

	control_margin = MarginContainer.new()
	control_margin.add_theme_constant_override("margin_left", 20)
	control_margin.add_theme_constant_override("margin_right", 20)
	control_margin.add_theme_constant_override("margin_top", 16)
	control_margin.add_theme_constant_override("margin_bottom", 16)
	control_panel.add_child(control_margin)

	var scroll := ScrollContainer.new()
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	scroll.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	# Reserve a dedicated gutter so the vertical rail never paints over cards.
	scroll.add_theme_constant_override("scrollbar_width", 12)
	control_margin.add_child(scroll)

	var scroll_content_margin := MarginContainer.new()
	scroll_content_margin.add_theme_constant_override("margin_right", 14)
	scroll_content_margin.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(scroll_content_margin)

	var root_column := VBoxContainer.new()
	root_column.add_theme_constant_override("separation", 8)
	root_column.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll_content_margin.add_child(root_column)

	var header_row := HBoxContainer.new()
	header_row.add_theme_constant_override("separation", 8)
	root_column.add_child(header_row)
	var title_column := VBoxContainer.new()
	title_column.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	header_row.add_child(title_column)
	header_title = _label("Quest 控制台", 24, COLOR_PARCHMENT)
	header_title.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	header_title.clip_text = true
	title_column.add_child(header_title)
	header_subtitle = _label("PROJECTTOWN · v1.0", 11, COLOR_MUTED)
	header_subtitle.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	header_subtitle.clip_text = true
	title_column.add_child(header_subtitle)
	tutorial_button = Button.new()
	tutorial_button.text = "使用教程"
	tutorial_button.tooltip_text = "查看从目标到成果确认的完整流程。"
	_apply_button_palette(tutorial_button, COLOR_WOOD, COLOR_PARCHMENT, COLOR_BORDER)
	tutorial_button.pressed.connect(_show_tutorial)
	header_row.add_child(tutorial_button)
	settings_button = Button.new()
	settings_button.text = "设置"
	settings_button.tooltip_text = "配置本地模型连接设置。"
	_apply_button_palette(settings_button, COLOR_STONE, COLOR_PARCHMENT, COLOR_BORDER)
	settings_button.pressed.connect(_show_settings)
	header_row.add_child(settings_button)
	backend_dot = _label("●", 16, Color("#b5bdc6"))
	header_row.add_child(backend_dot)
	backend_label = _label("连接中", 12, COLOR_MUTED)
	backend_label.custom_minimum_size.x = 72.0
	header_row.add_child(backend_label)

	onboarding_panel = PanelContainer.new()
	onboarding_panel.add_theme_stylebox_override(
		"panel", _pixel_frame(Color("#2b4d58"), COLOR_PIXEL_BLACK, COLOR_ACCENT, 1, 8)
	)
	root_column.add_child(onboarding_panel)
	var onboarding_row := HBoxContainer.new()
	onboarding_row.add_theme_constant_override("separation", 8)
	onboarding_panel.add_child(onboarding_row)
	onboarding_portrait = TextureRect.new()
	onboarding_portrait.custom_minimum_size = Vector2(64.0, 64.0)
	onboarding_portrait.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	onboarding_portrait.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	onboarding_portrait.texture_filter = Control.TEXTURE_FILTER_NEAREST
	onboarding_portrait.tooltip_text = "信使会随 Quest 进入规划、执行或验收阶段。"
	onboarding_row.add_child(onboarding_portrait)
	var onboarding_column := VBoxContainer.new()
	onboarding_column.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	onboarding_column.add_theme_constant_override("separation", 3)
	onboarding_row.add_child(onboarding_column)
	onboarding_step_label = _label("第 1 步 · 描述你想完成的事情", 13, COLOR_PARCHMENT)
	onboarding_step_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	onboarding_step_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	onboarding_column.add_child(onboarding_step_label)
	onboarding_help_label = _label(
		"选择模板或填写目标 → 创建并审核 → 运行 → 预览成果 → 保留或丢弃",
		12,
		COLOR_MUTED
	)
	onboarding_help_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	onboarding_column.add_child(onboarding_help_label)
	_update_onboarding_portrait("idle")

	var history_panel := PanelContainer.new()
	history_panel.add_theme_stylebox_override(
		"panel", _pixel_frame(Color("#263c4d"), COLOR_PIXEL_BLACK, COLOR_BORDER, 1, 6)
	)
	root_column.add_child(history_panel)
	var history_column := VBoxContainer.new()
	history_column.add_theme_constant_override("separation", 4)
	history_panel.add_child(history_column)
	history_column.add_child(_label("历史 / 继续任务", 13, COLOR_INK))
	history_query = LineEdit.new()
	history_query.placeholder_text = "搜索 Quest ID 或目标"
	history_query.text_submitted.connect(func(_text: String) -> void: _request_history_page(0))
	history_column.add_child(history_query)
	var history_filter_row := HBoxContainer.new()
	history_filter_row.add_theme_constant_override("separation", 6)
	history_column.add_child(history_filter_row)
	history_status_menu = MenuButton.new()
	history_status_menu.text = "状态筛选（多选）"
	for index in range(HISTORY_STATUSES.size()):
		history_status_menu.get_popup().add_check_item(HISTORY_STATUSES[index], index)
	history_status_menu.get_popup().id_pressed.connect(_on_history_status_toggled)
	history_filter_row.add_child(history_status_menu)
	history_refresh_button = Button.new()
	history_refresh_button.text = "刷新/重试"
	history_refresh_button.pressed.connect(func() -> void: _request_history_page(0))
	history_filter_row.add_child(history_refresh_button)
	var history_row := HBoxContainer.new()
	history_row.add_theme_constant_override("separation", 8)
	history_column.add_child(history_row)
	history_select = OptionButton.new()
	history_select.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	history_select.fit_to_longest_item = false
	history_select.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	history_select.clip_text = true
	history_select.custom_minimum_size.y = 34.0
	history_select.disabled = true
	history_select.add_item("正在读取已有 Quest…")
	history_row.add_child(history_select)
	restore_quest_button = Button.new()
	restore_quest_button.text = "继续任务"
	restore_quest_button.tooltip_text = "仅加载所选 Quest 的状态、轨迹、证据和成果；不会自动执行、保留或丢弃。"
	restore_quest_button.disabled = true
	_apply_button_palette(restore_quest_button, COLOR_STONE, COLOR_PARCHMENT, COLOR_BORDER)
	restore_quest_button.pressed.connect(_on_restore_quest_pressed)
	history_row.add_child(restore_quest_button)
	history_status_label = _label("可选择已创建的 Quest 继续查看。", 11, COLOR_MUTED)
	history_status_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	history_column.add_child(history_status_label)
	var history_paging_row := HBoxContainer.new()
	history_paging_row.add_theme_constant_override("separation", 6)
	history_column.add_child(history_paging_row)
	history_previous_button = Button.new()
	history_previous_button.text = "上一页"
	history_previous_button.pressed.connect(func() -> void: _request_history_page(maxi(0, history_offset - HISTORY_PAGE_SIZE)))
	history_paging_row.add_child(history_previous_button)
	history_page_label = _label("第 1 页 · 0 条", 11, COLOR_MUTED)
	history_page_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	history_page_label.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	history_paging_row.add_child(history_page_label)
	history_next_button = Button.new()
	history_next_button.text = "下一页"
	history_next_button.pressed.connect(func() -> void: _request_history_page(history_offset + HISTORY_PAGE_SIZE))
	history_paging_row.add_child(history_next_button)
	failure_detail_label = RichTextLabel.new()
	failure_detail_label.bbcode_enabled = true
	failure_detail_label.fit_content = true
	failure_detail_label.custom_minimum_size.y = 42.0
	failure_detail_label.text = "[color=#8390a1]失败详情会在只读恢复后显示。[/color]"
	history_column.add_child(failure_detail_label)

	root_column.add_child(_label("目标", 13, COLOR_INK))
	goal_input = TextEdit.new()
	goal_input.custom_minimum_size.y = 80.0
	goal_input.placeholder_text = "例如：读取三份 Agent 学习笔记，生成结构完整的 Markdown 报告"
	goal_input.wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY
	goal_input.add_theme_font_size_override("font_size", 14)
	goal_input.add_theme_color_override("font_color", COLOR_PARCHMENT)
	goal_input.add_theme_color_override("font_placeholder_color", Color("#93a2ae"))
	goal_input.add_theme_stylebox_override("normal", _input_box())
	goal_input.add_theme_stylebox_override("focus", _input_box(COLOR_PRIMARY))
	root_column.add_child(goal_input)

	options_row = HBoxContainer.new()
	options_row.add_theme_constant_override("separation", 8)
	root_column.add_child(options_row)
	var template_column := VBoxContainer.new()
	template_column.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	template_column.add_theme_constant_override("separation", 4)
	options_row.add_child(template_column)
	template_column.add_child(_label("模板", 12, COLOR_MUTED))
	template_select = OptionButton.new()
	template_select.custom_minimum_size.y = 38.0
	template_select.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	template_select.add_theme_font_size_override("font_size", 13)
	template_select.item_selected.connect(_on_template_selected)
	template_column.add_child(template_select)
	template_help_label = _label("", 12, COLOR_MUTED)
	template_help_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	template_column.add_child(template_help_label)
	workspace_column = VBoxContainer.new()
	workspace_column.custom_minimum_size.x = 124.0
	workspace_column.add_theme_constant_override("separation", 4)
	options_row.add_child(workspace_column)
	workspace_column.add_child(_label("工作区", 12, COLOR_MUTED))
	workspace_input = LineEdit.new()
	workspace_input.text = "自动创建"
	workspace_input.placeholder_text = "每个 Quest 独立保存"
	workspace_input.editable = false
	workspace_input.tooltip_text = "为避免误删或覆盖，客户端会为每个 Quest 创建独立工作区。"
	workspace_input.custom_minimum_size.y = 38.0
	workspace_input.add_theme_font_size_override("font_size", 13)
	workspace_input.add_theme_stylebox_override("normal", _input_box())
	workspace_input.add_theme_stylebox_override("focus", _input_box(COLOR_PRIMARY))
	workspace_column.add_child(workspace_input)

	action_row = HBoxContainer.new()
	action_row.add_theme_constant_override("separation", 8)
	root_column.add_child(action_row)
	create_button = Button.new()
	create_button.text = "下一步：创建任务草案"
	create_button.custom_minimum_size.y = 40.0
	create_button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	create_button.add_theme_font_size_override("font_size", 14)
	_apply_button_palette(create_button, COLOR_PRIMARY, COLOR_PARCHMENT, COLOR_ACCENT)
	create_button.pressed.connect(_on_create_pressed)
	action_row.add_child(create_button)
	refresh_button = Button.new()
	refresh_button.text = "刷新"
	refresh_button.custom_minimum_size = Vector2(70.0, 40.0)
	refresh_button.disabled = true
	_apply_button_palette(refresh_button, COLOR_STONE, COLOR_PARCHMENT, COLOR_BORDER)
	refresh_button.pressed.connect(_refresh_active_quest)
	action_row.add_child(refresh_button)
	control_button = Button.new()
	control_button.text = "暂停"
	control_button.custom_minimum_size = Vector2(70.0, 40.0)
	control_button.disabled = true
	_apply_button_palette(control_button, COLOR_WOOD, COLOR_PARCHMENT, COLOR_ACCENT)
	control_button.pressed.connect(_on_control_pressed)
	action_row.add_child(control_button)

	decision_row = HBoxContainer.new()
	decision_row.add_theme_constant_override("separation", 8)
	root_column.add_child(decision_row)
	approve_button = Button.new()
	approve_button.text = "批准"
	approve_button.disabled = true
	_apply_button_palette(approve_button, COLOR_SUCCESS, COLOR_PARCHMENT, Color("#9bd587"))
	approve_button.pressed.connect(_on_approve_pressed)
	decision_row.add_child(approve_button)
	modify_button = Button.new()
	modify_button.text = "修改目标"
	modify_button.disabled = true
	_apply_button_palette(modify_button, COLOR_WOOD, COLOR_PARCHMENT, COLOR_ACCENT)
	modify_button.pressed.connect(_on_modify_pressed)
	decision_row.add_child(modify_button)
	reject_button = Button.new()
	reject_button.text = "拒绝任务"
	reject_button.disabled = true
	_apply_button_palette(reject_button, COLOR_DANGER, COLOR_PARCHMENT, Color("#e49a84"))
	reject_button.pressed.connect(_on_reject_pressed)
	decision_row.add_child(reject_button)

	root_column.add_child(HSeparator.new())

	var summary_row := HBoxContainer.new()
	summary_row.add_theme_constant_override("separation", 8)
	root_column.add_child(summary_row)
	var summary_column := VBoxContainer.new()
	summary_column.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	summary_row.add_child(summary_column)
	quest_id_label = _label("尚未创建 Quest", 12, COLOR_MUTED)
	quest_id_label.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	summary_column.add_child(quest_id_label)
	current_step_label = _label("等待目标", 15, COLOR_INK)
	current_step_label.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	summary_column.add_child(current_step_label)
	status_badge_panel = PanelContainer.new()
	status_badge_panel.custom_minimum_size = Vector2(82.0, 30.0)
	status_badge_panel.add_theme_stylebox_override("panel", _style_box(COLOR_STONE, COLOR_BORDER, 0, 2, 4))
	status_badge = _label("IDLE", 11, COLOR_PARCHMENT)
	status_badge.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	status_badge.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	status_badge_panel.add_child(status_badge)
	summary_row.add_child(status_badge_panel)

	progress_bar = ProgressBar.new()
	progress_bar.min_value = 0.0
	progress_bar.max_value = 100.0
	progress_bar.value = 0.0
	progress_bar.show_percentage = true
	progress_bar.custom_minimum_size.y = 22.0
	progress_bar.add_theme_font_size_override("font_size", 13)
	progress_bar.add_theme_stylebox_override("background", _pixel_frame(COLOR_DEEP_STONE, COLOR_PIXEL_BLACK, COLOR_BORDER, 1, 2))
	progress_bar.add_theme_stylebox_override("fill", _pixel_frame(COLOR_PRIMARY, COLOR_PIXEL_BLACK, Color("#8fd3d5"), 1, 2))
	root_column.add_child(progress_bar)

	root_column.add_child(_section_title("Goal Contract"))
	contract_summary = RichTextLabel.new()
	contract_summary.fit_content = false
	contract_summary.scroll_active = true
	contract_summary.custom_minimum_size.y = 68.0
	contract_summary.add_theme_font_size_override("normal_font_size", 13)
	contract_summary.add_theme_color_override("default_color", COLOR_PARCHMENT)
	contract_summary.add_theme_stylebox_override("normal", _style_box(COLOR_DEEP_STONE, COLOR_BORDER, 0, 2, 7))
	contract_summary.text = "创建 Quest 后可在此复核目标、约束、非目标、验收标准与预算。"
	root_column.add_child(contract_summary)

	root_column.add_child(_section_title("成果预览与确认"))
	result_panel = PanelContainer.new()
	result_panel.add_theme_stylebox_override(
		"panel", _pixel_frame(Color("#263c4d"), COLOR_PIXEL_BLACK, COLOR_ACCENT, 1, 8)
	)
	root_column.add_child(result_panel)
	var result_column := VBoxContainer.new()
	result_column.add_theme_constant_override("separation", 5)
	result_panel.add_child(result_column)
	result_state_label = _label("任务完成后，成果会直接显示在这里。", 13, COLOR_MUTED)
	result_state_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	result_column.add_child(result_state_label)
	artifact_provenance_label = _label("", 12, COLOR_MUTED)
	artifact_provenance_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	artifact_provenance_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	artifact_provenance_label.visible = false
	result_column.add_child(artifact_provenance_label)
	result_select = OptionButton.new()
	result_select.custom_minimum_size.y = 30.0
	result_select.disabled = true
	result_select.item_selected.connect(_on_result_selected)
	result_select.add_item("尚无成果")
	result_column.add_child(result_select)
	result_preview = TextEdit.new()
	result_preview.editable = false
	result_preview.wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY
	result_preview.custom_minimum_size.y = 112.0
	result_preview.add_theme_font_size_override("font_size", 13)
	result_preview.add_theme_color_override("font_color", COLOR_PARCHMENT)
	result_preview.add_theme_stylebox_override("normal", _style_box(COLOR_DEEP_STONE, COLOR_BORDER, 0, 2, 6))
	result_preview.text = "等待 Agent 生成并验证成果…"
	result_preview.gui_input.connect(_on_result_preview_gui_input)
	var preview_toolbar := HBoxContainer.new()
	preview_toolbar.add_theme_constant_override("separation", 6)
	var preview_hint := _label("双击正文可查看详情", 12, COLOR_MUTED)
	preview_hint.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	preview_toolbar.add_child(preview_hint)
	artifact_preview_detail_button = Button.new()
	artifact_preview_detail_button.text = "放大查看"
	artifact_preview_detail_button.tooltip_text = "在只读窗口中查看完整成果内容。"
	artifact_preview_detail_button.disabled = true
	artifact_preview_detail_button.pressed.connect(_show_artifact_preview_detail)
	preview_toolbar.add_child(artifact_preview_detail_button)
	result_column.add_child(preview_toolbar)
	result_column.add_child(result_preview)
	var result_actions := HBoxContainer.new()
	result_actions.add_theme_constant_override("separation", 8)
	result_column.add_child(result_actions)
	keep_result_button = Button.new()
	keep_result_button.text = "保留成果"
	keep_result_button.disabled = true
	keep_result_button.tooltip_text = "确认内容无误后，将成果保留在此 Quest 的独立工作区。"
	keep_result_button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_apply_button_palette(keep_result_button, COLOR_SUCCESS, COLOR_PARCHMENT, Color("#9bd587"))
	keep_result_button.pressed.connect(_on_keep_result_pressed)
	result_actions.add_child(keep_result_button)
	discard_result_button = Button.new()
	discard_result_button.text = "丢弃成果"
	discard_result_button.disabled = true
	discard_result_button.tooltip_text = "仅删除本 Quest 新创建且内容未被改动的成果。"
	discard_result_button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_apply_button_palette(discard_result_button, COLOR_DANGER, COLOR_PARCHMENT, Color("#e49a84"))
	discard_result_button.pressed.connect(_on_discard_result_pressed)
	result_actions.add_child(discard_result_button)
	discard_confirmation = ConfirmationDialog.new()
	discard_confirmation.title = "确认丢弃成果"
	discard_confirmation.dialog_text = "这些成果会从本 Quest 的独立工作区删除，无法在界面中恢复。确定继续吗？"
	discard_confirmation.ok_button_text = "确认丢弃"
	discard_confirmation.cancel_button_text = "返回预览"
	_style_dialog(discard_confirmation, COLOR_DANGER)
	discard_confirmation.confirmed.connect(_confirm_discard_result)
	add_child(discard_confirmation)
	artifact_preview_dialog = Window.new()
	artifact_preview_dialog.title = "成果详情"
	artifact_preview_dialog.visible = false
	artifact_preview_dialog.transient = true
	# Keep review actions behind the detail view inaccessible until the user
	# closes it, preventing an accidental retain/discard while reading.
	artifact_preview_dialog.exclusive = true
	artifact_preview_dialog.close_requested.connect(artifact_preview_dialog.hide)
	artifact_preview_dialog.window_input.connect(_on_artifact_preview_dialog_input)
	add_child(artifact_preview_dialog)
	var detail_margin := MarginContainer.new()
	detail_margin.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	detail_margin.add_theme_constant_override("margin_left", 12)
	detail_margin.add_theme_constant_override("margin_top", 12)
	detail_margin.add_theme_constant_override("margin_right", 12)
	detail_margin.add_theme_constant_override("margin_bottom", 12)
	artifact_preview_dialog.add_child(detail_margin)
	artifact_preview_dialog_text = TextEdit.new()
	artifact_preview_dialog_text.editable = false
	artifact_preview_dialog_text.wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY
	artifact_preview_dialog_text.focus_mode = Control.FOCUS_ALL
	artifact_preview_dialog_text.size_flags_vertical = Control.SIZE_EXPAND_FILL
	artifact_preview_dialog_text.add_theme_font_size_override("font_size", 14)
	artifact_preview_dialog_text.add_theme_color_override("font_color", COLOR_PARCHMENT)
	artifact_preview_dialog_text.add_theme_stylebox_override("normal", _style_box(COLOR_DEEP_STONE, COLOR_BORDER, 0, 2, 6))
	detail_margin.add_child(artifact_preview_dialog_text)
	tutorial_dialog = AcceptDialog.new()
	tutorial_dialog.title = "ProjectTown 使用教程"
	tutorial_dialog.dialog_text = (
		"ProjectTown 会把你的目标变成一条可追踪、可验收的 Agent Quest。\n\n"
		+ "1. 选择模板并描述目标，创建任务草案。\n"
		+ "2. 检查 Goal Contract，确认后再运行。\n"
		+ "3. 在小镇、里程碑和运行轨迹中查看执行过程。\n"
		+ "4. 验收通过后逐个预览真实文件内容。满意就保留；不满意可安全丢弃。\n\n"
		+ "每个 Quest 使用独立工作区；丢弃只会删除该 Quest 新建且内容未改变的成果。"
	)
	tutorial_dialog.ok_button_text = "开始使用"
	_style_dialog(tutorial_dialog, COLOR_PRIMARY)
	add_child(tutorial_dialog)
	_build_settings_dialog()

	root_column.add_child(_section_title("Evidence"))
	evidence_list = ItemList.new()
	evidence_list.custom_minimum_size.y = 56.0
	evidence_list.add_theme_font_size_override("font_size", 13)
	evidence_list.add_theme_stylebox_override("panel", _style_box(COLOR_DEEP_STONE, COLOR_BORDER, 0, 2, 4))
	evidence_list.add_item("尚无验收证据")
	evidence_list.set_item_disabled(0, true)
	root_column.add_child(evidence_list)

	root_column.add_child(_section_title("里程碑"))
	milestones_list = ItemList.new()
	milestones_list.custom_minimum_size.y = 90.0
	milestones_list.size_flags_vertical = Control.SIZE_EXPAND_FILL
	milestones_list.allow_reselect = true
	milestones_list.add_theme_font_size_override("font_size", 12)
	milestones_list.add_theme_stylebox_override("panel", _style_box(COLOR_DEEP_STONE, COLOR_BORDER, 0, 2, 4))
	milestones_list.add_item("○ 创建 Quest 后显示执行步骤")
	milestones_list.set_item_disabled(0, true)
	root_column.add_child(milestones_list)

	root_column.add_child(_section_title("运行轨迹"))
	trace_log = RichTextLabel.new()
	trace_log.bbcode_enabled = true
	trace_log.fit_content = false
	trace_log.scroll_active = true
	trace_log.scroll_following = true
	trace_log.custom_minimum_size.y = 100.0
	trace_log.size_flags_vertical = Control.SIZE_EXPAND_FILL
	trace_log.add_theme_font_size_override("normal_font_size", 13)
	trace_log.add_theme_color_override("default_color", COLOR_PARCHMENT)
	trace_log.add_theme_stylebox_override("normal", _style_box(COLOR_DEEP_STONE, COLOR_BORDER, 0, 2, 7))
	trace_log.text = "[color=#aab8c5]后端 Trace 将显示在这里。[/color]"
	root_column.add_child(trace_log)

	endpoint_hint = _label(server_base_url, 11, COLOR_MUTED)
	endpoint_hint.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	endpoint_hint.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	endpoint_hint.clip_text = true
	endpoint_hint.tooltip_text = "可在 main.tscn 的 server_base_url 修改后端地址"
	root_column.add_child(endpoint_hint)


func _setup_api() -> void:
	api = ProjectTownAPIClass.new()
	api.server_base_url = server_base_url
	add_child(api)
	api.health_received.connect(_on_health_received)
	api.templates_received.connect(_on_templates_received)
	api.quests_received.connect(_on_quests_received)
	api.quest_history_loading.connect(_on_history_loading)
	api.quest_history_received.connect(_on_history_received)
	api.quest_history_error.connect(_on_history_error)
	api.quest_failure_received.connect(_on_failure_received)
	api.quest_failure_error.connect(_on_failure_error)
	api.quest_created.connect(_on_quest_created)
	api.quest_confirmed.connect(_on_quest_confirmed)
	api.quest_started.connect(_on_quest_started)
	api.quest_controlled.connect(_on_quest_controlled)
	api.decision_submitted.connect(_on_decision_submitted)
	api.quest_received.connect(_on_quest_received)
	api.events_received.connect(_on_traces_received)
	api.event_received.connect(_on_live_event)
	api.evidence_received.connect(_on_evidence_received)
	api.artifacts_received.connect(_on_artifacts_received)
	api.artifact_preview_received.connect(_on_artifact_preview_received)
	api.artifact_reviewed.connect(_on_artifact_reviewed)
	api.settings_received.connect(_on_settings_received)
	api.settings_saved.connect(_on_settings_saved)
	api.websocket_state.connect(_on_websocket_state)


func _install_poll_timer(start: bool = true) -> void:
	poll_timer = Timer.new()
	poll_timer.wait_time = poll_interval_seconds
	poll_timer.one_shot = false
	poll_timer.timeout.connect(_on_poll_timeout)
	add_child(poll_timer)
	if start:
		poll_timer.start()


func _load_fallback_templates() -> void:
	templates = [
		{
			"id": "project_brief",
			"name": "项目简报",
			"description": "把个人项目目标整理为包含范围、里程碑和验收清单的 Markdown 简报",
			"goal_example": "为我的个人 Agent 项目创建一份可执行的项目简报",
		},
		{
			"id": "python_starter",
			"name": "Python 起步项目",
			"description": "创建一个可通过语法检查的最小 Python CLI，并补充 README",
			"goal_example": "创建一个最小 Python CLI 起步项目",
		},
		{
			"id": "readme_builder",
			"name": "README 构建器",
			"description": "根据目标生成结构清晰、可检查的项目 README",
			"goal_example": "为我的项目生成一份包含使用方法和路线图的 README",
		},
	]
	_render_templates()
	template_select.select(0)
	_on_template_selected(0)


func _render_templates() -> void:
	template_select.clear()
	for item in templates:
		var title := str(item.get("name", item.get("title", item.get("id", "未命名模板"))))
		var item_id := str(item.get("id", item.get("template_id", "")))
		template_select.add_item(title)
		template_select.set_item_metadata(template_select.item_count - 1, item_id)
		template_select.set_item_tooltip(template_select.item_count - 1, str(item.get("description", "")))


func _on_template_selected(index: int) -> void:
	if index < 0 or index >= templates.size():
		return
	var suggested_goal := str(templates[index].get("goal_example", templates[index].get("default_goal", templates[index].get("goal", ""))))
	template_help_label.text = str(templates[index].get("description", ""))
	var current_goal := goal_input.text.strip_edges()
	if not suggested_goal.is_empty() and (current_goal.is_empty() or current_goal == last_suggested_goal):
		goal_input.text = suggested_goal
	last_suggested_goal = suggested_goal


func _on_create_pressed() -> void:
	var goal := goal_input.text.strip_edges()
	if active_status == "draft" and not active_quest_id.is_empty():
		if goal.is_empty():
			_show_local_error("确认前请填写目标")
			return
		create_button.disabled = true
		create_button.text = "正在确认…"
		api.confirm_quest(active_quest_id, state_version, contract_version, true, goal)
		return
	var template_id := ""
	if template_select.selected >= 0:
		template_id = str(template_select.get_item_metadata(template_select.selected))
	if goal.is_empty() and template_id.is_empty():
		_show_local_error("请输入目标或选择一个模板")
		goal_input.grab_focus()
		return
	create_button.disabled = true
	create_button.text = "正在创建…"
	trace_log.text = "[color=#66748a]正在创建 Quest…[/color]"
	last_trace_sequence = 0
	event_lines.clear()
	seen_event_sequences.clear()
	last_evidence_count = 0
	artifact_items.clear()
	active_artifact_id = ""
	result_review_key = ""
	result_manifest_hash = ""
	artifact_review_pending = false
	final_projection_requested = false
	_reset_result_panel()
	pending_action_id = ""
	evidence_list.clear()
	evidence_list.add_item("尚无验收证据")
	evidence_list.set_item_disabled(0, true)
	api.last_sequence = 0
	api.disconnect_events()
	api.create_quest(goal, template_id)


func _on_health_received(success: bool, data: Dictionary, message: String) -> void:
	if success:
		var version := str(data.get("version", "v1.0"))
		_set_backend_state(true, "在线 · %s" % version)
	else:
		_set_backend_state(false, "后端离线")
		_show_local_error(message)


func _on_templates_received(success: bool, items: Array, message: String) -> void:
	if not success:
		# Offline fallback is intentional: users can still inspect and fill the UI.
		return
	var normalized: Array[Dictionary] = []
	for item in items:
		if item is Dictionary:
			normalized.append(item as Dictionary)
	if normalized.is_empty():
		return
	templates = normalized
	_render_templates()
	template_select.select(0)
	_on_template_selected(0)


func _on_quests_received(success: bool, items: Array, message: String) -> void:
	if transport_enabled:
		return
	if history_select == null:
		return
	history_select.clear()
	if not success:
		history_select.add_item("无法读取历史 Quest")
		history_select.disabled = true
		restore_quest_button.disabled = true
		history_status_label.text = "历史任务读取失败：%s" % message
		return
	var valid_items: Array[Dictionary] = []
	for raw in items:
		if raw is Dictionary:
			var quest := raw as Dictionary
			if not str(quest.get("id", quest.get("quest_id", ""))).is_empty():
				valid_items.append(quest)
	if valid_items.is_empty():
		history_select.add_item("尚无已创建的 Quest")
		history_select.disabled = true
		restore_quest_button.disabled = true
		history_status_label.text = "创建后的 Quest 会显示在这里，供下次继续查看。"
		return
	for quest in valid_items:
		var quest_id := str(quest.get("id", quest.get("quest_id", "")))
		var status := str(quest.get("status", "unknown")).to_upper()
		var goal := str(quest.get("goal", "未命名目标")).replace("\n", " ")
		var full_text := "[%s] %s — %s" % [status, quest_id, goal]
		var display_text := "[%s] %s — %s" % [status, _short_id(quest_id), goal.left(32)]
		history_select.add_item(display_text)
		history_select.set_item_tooltip(history_select.item_count - 1, full_text)
		history_select.set_item_metadata(history_select.item_count - 1, quest_id)
	history_select.disabled = false
	restore_quest_button.disabled = false
	history_status_label.text = "选择一项后点击“继续任务”；不会自动提交任何决定。"


func _on_restore_quest_pressed() -> void:
	if history_select == null or history_select.disabled or history_select.selected < 0:
		return
	var quest_id := str(history_select.get_item_metadata(history_select.selected))
	if quest_id.is_empty():
		return
	_activate_quest(quest_id)
	if transport_enabled and api != null:
		api.fetch_failure(quest_id)
	refresh_button.disabled = false
	if poll_timer != null:
		poll_timer.start()
	history_status_label.text = "正在恢复 %s 的状态与成果…" % _short_id(quest_id)
	_refresh_active_quest()


func _history_statuses() -> Array[String]:
	var selected: Array[String] = []
	if history_status_menu == null:
		return selected
	var popup := history_status_menu.get_popup()
	for index in range(HISTORY_STATUSES.size()):
		if popup.is_item_checked(index):
			selected.append(HISTORY_STATUSES[index])
	return selected


func _on_history_status_toggled(index: int) -> void:
	if history_status_menu == null:
		return
	var popup := history_status_menu.get_popup()
	popup.set_item_checked(index, not popup.is_item_checked(index))
	_request_history_page(0)


func _request_history_page(offset: int = 0) -> void:
	history_generation += 1
	history_offset = maxi(0, offset)
	var context := {"generation": history_generation, "offset": history_offset, "limit": HISTORY_PAGE_SIZE}
	_on_history_loading(context)
	if transport_enabled and api != null:
		api.fetch_quest_history(history_query.text.strip_edges() if history_query != null else "", _history_statuses(), history_offset, HISTORY_PAGE_SIZE, context)


func _on_history_loading(context: Dictionary) -> void:
	if int(context.get("generation", -1)) != history_generation:
		return
	if history_status_label != null:
		history_status_label.text = "正在读取历史 Quest…"
	if history_refresh_button != null:
		history_refresh_button.disabled = true


func _on_history_received(success: bool, items: Array, total: int, message: String, context: Dictionary) -> void:
	if int(context.get("generation", -1)) != history_generation:
		return
	history_total = maxi(0, total)
	if history_refresh_button != null:
		history_refresh_button.disabled = false
	if not success:
		_on_history_error(message, context)
		return
	var selected_id := ""
	if history_select != null and history_select.selected >= 0:
		selected_id = str(history_select.get_item_metadata(history_select.selected))
	_populate_history_page(items, selected_id)


func _on_history_error(message: String, context: Dictionary) -> void:
	if int(context.get("generation", -1)) != history_generation:
		return
	if history_refresh_button != null:
		history_refresh_button.disabled = false
	if history_status_label != null:
		history_status_label.text = "历史读取失败：%s；可点击刷新/重试。" % message


func _populate_history_page(items: Array, previous_selection: String = "") -> void:
	if history_select == null:
		return
	history_select.clear()
	var restored := false
	for raw in items:
		if not raw is Dictionary:
			continue
		var quest := raw as Dictionary
		var quest_id := str(quest.get("id", quest.get("quest_id", "")))
		if quest_id.is_empty():
			continue
		var status := str(quest.get("status", "unknown")).to_upper()
		var goal := str(quest.get("goal", quest.get("contract", {}).get("goal", "未命名目标"))).replace("\n", " ")
		var review := bool(quest.get("artifact_review_required", false)) and str(quest.get("status", "")) == "waiting_user"
		var code := str((quest.get("error", {}) as Dictionary).get("code", "")) if quest.get("error", {}) is Dictionary else ""
		var suffix := " · 待成果审核" if review else (" · %s" % code if not code.is_empty() else "")
		history_select.add_item("[%s] %s — %s%s" % [status, _short_id(quest_id), goal.left(24), suffix])
		history_select.set_item_metadata(history_select.item_count - 1, quest_id)
		if quest_id == previous_selection:
			history_select.select(history_select.item_count - 1)
			restored = true
	if history_select.item_count == 0:
		history_select.add_item("尚无匹配 Quest")
		history_select.disabled = true
		restore_quest_button.disabled = true
	elif not restored:
		history_select.select(0)
		history_select.disabled = false
		restore_quest_button.disabled = false
	if history_page_label != null:
		history_page_label.text = "第 %d 页 · 共 %d 条" % [int(history_offset / HISTORY_PAGE_SIZE) + 1, history_total]
	if history_previous_button != null:
		history_previous_button.disabled = history_offset <= 0
	if history_next_button != null:
		history_next_button.disabled = history_offset + HISTORY_PAGE_SIZE >= history_total
	if history_status_label != null:
		history_status_label.text = "已刷新历史；所选 Quest 不在本页时已切换为当前页首项。" if not previous_selection.is_empty() and not restored else "选择一项后点击“继续任务”；不会自动提交任何决定。"


func _on_failure_received(success: bool, data: Dictionary, message: String, source_quest_id: String) -> void:
	if source_quest_id != active_quest_id or failure_detail_label == null:
		return
	if not success:
		failure_detail_label.text = "[color=#bd5b55]失败详情读取失败：%s[/color]" % _escape_bbcode(message)
		return
	var summary: Dictionary = data.get("summary", {}) if data.get("summary", {}) is Dictionary else {}
	var navigation: Dictionary = data.get("navigation", {}) if data.get("navigation", {}) is Dictionary else {}
	var refs := "里程碑：%s · 证据：%d" % [str(navigation.get("milestone_id", "—")), (navigation.get("evidence_ids", []) as Array).size()]
	failure_detail_label.text = "[color=#f0dfae]%s / %s[/color]\n%s\n[color=#8390a1]%s[/color]" % [_escape_bbcode(str(summary.get("category", "internal_runtime"))), _escape_bbcode(str(summary.get("code", "FAILURE_CONTEXT_UNAVAILABLE"))), _escape_bbcode(str(summary.get("message", "暂无失败详情。"))), _escape_bbcode(refs)]


func _on_failure_error(message: String, source_quest_id: String) -> void:
	_on_failure_received(false, {}, message, source_quest_id)


func _on_quest_created(success: bool, quest: Dictionary, message: String) -> void:
	if not success:
		_reset_create_button()
		_show_local_error("创建失败：%s" % message)
		return
	var created_quest_id := str(quest.get("id", quest.get("quest_id", "")))
	if created_quest_id.is_empty():
		_reset_create_button()
		_show_local_error("创建响应中缺少 Quest ID")
		return
	_activate_quest(created_quest_id)
	api.fetch_quests()
	refresh_button.disabled = false
	_apply_quest(quest)
	trace_log.text = "[color=#4a78b7]Goal Contract 草案已创建。请复核目标后点击确认并运行。[/color]"
	create_button.disabled = false
	create_button.text = "第 2 步：确认并运行"
	_update_onboarding("draft")

func _on_quest_confirmed(success: bool, quest: Dictionary, message: String, source_quest_id: String) -> void:
	if source_quest_id != active_quest_id:
		return
	if not success:
		_reset_create_button()
		_show_local_error("确认失败：%s" % message)
		return
	if str(quest.get("id", quest.get("quest_id", ""))) != active_quest_id:
		return
	_apply_quest(quest)
	trace_log.text = "[color=#4a78b7]Goal Contract 已确认，正在启动…[/color]"
	api.call_deferred("run_quest", active_quest_id, state_version)


func _on_quest_started(success: bool, data: Dictionary, message: String, source_quest_id: String) -> void:
	if source_quest_id != active_quest_id:
		return
	if not success:
		_reset_create_button()
		_show_local_error("启动失败：%s" % message)
		return
	if str(data.get("id", data.get("quest_id", ""))) != active_quest_id:
		return
	active_status = str(data.get("status", "running")).to_lower()
	state_version = int(data.get("state_version", state_version))
	_update_onboarding(active_status)
	create_button.disabled = true
	create_button.text = "Agent 正在执行…"
	status_badge.text = active_status.to_upper()
	_apply_status_style(active_status)
	town_view.set_quest_state(active_status, progress_bar.value / 100.0, goal_input.text.strip_edges())
	# Keep RUNNING visible for at least one polling interval even when the
	# deterministic local backend finishes in a fraction of a second.
	poll_timer.start(poll_interval_seconds)


func _on_control_pressed() -> void:
	if active_quest_id.is_empty():
		return
	control_button.disabled = true
	if active_status in ["paused", "recovering"]:
		control_button.text = "恢复中…"
		api.resume_quest(active_quest_id, state_version)
	else:
		control_button.text = "暂停中…"
		api.pause_quest(active_quest_id, state_version)


func _on_quest_controlled(success: bool, data: Dictionary, message: String, source_quest_id: String) -> void:
	if source_quest_id != active_quest_id:
		return
	if not success:
		_show_local_error("控制失败：%s" % message)
		_update_control_button()
		return
	if str(data.get("id", data.get("quest_id", ""))) != active_quest_id:
		return
	_apply_quest(data)
	_refresh_active_quest()


func _on_approve_pressed() -> void:
	if active_status != "waiting_user":
		return
	var patch := {} if pending_action_id.is_empty() else {"action_id": pending_action_id}
	_set_decision_buttons_disabled(true)
	api.submit_decision(active_quest_id, "approve", state_version, "Approved in ProjectTown UI", patch)


func _on_modify_pressed() -> void:
	if active_status != "waiting_user":
		return
	var goal := goal_input.text.strip_edges()
	if goal.is_empty():
		_show_local_error("修改后的目标不能为空")
		return
	_set_decision_buttons_disabled(true)
	api.submit_decision(active_quest_id, "modify", state_version, "Goal updated in ProjectTown UI", {"goal": goal})


func _on_reject_pressed() -> void:
	if active_status != "waiting_user":
		return
	_set_decision_buttons_disabled(true)
	api.submit_decision(active_quest_id, "reject", state_version, "Rejected in ProjectTown UI")


func _on_decision_submitted(success: bool, data: Dictionary, message: String, source_quest_id: String) -> void:
	if source_quest_id != active_quest_id:
		return
	if not success:
		_show_local_error("决策提交失败：%s" % message)
		_update_control_button()
		return
	if str(data.get("id", data.get("quest_id", ""))) != active_quest_id:
		return
	_apply_quest(data)
	_refresh_active_quest()


func _activate_quest(quest_id: String) -> void:
	if quest_id == active_quest_id:
		return
	api.disconnect_events()
	active_quest_id = quest_id
	api.reset_quest_cursor(quest_id)
	# A restored Quest can legitimately have an earlier state version than the
	# Quest viewed before it. Reset the local version before its first response.
	active_status = "idle"
	state_version = 0
	contract_version = 1
	budget_usage.clear()
	last_trace_sequence = 0
	event_lines.clear()
	seen_event_sequences.clear()
	last_evidence_count = 0
	artifact_items.clear()
	active_artifact_id = ""
	result_review_key = ""
	result_manifest_hash = ""
	artifact_review_pending = false
	final_projection_requested = false
	pending_action_id = ""
	projection_poll_ticks = 0
	websocket_retry_seconds = 1.0
	next_websocket_retry_msec = 0
	evidence_list.clear()
	evidence_list.add_item("尚无验收证据")
	evidence_list.set_item_disabled(0, true)
	_reset_result_panel()


func _stop_automatic_updates() -> void:
	poll_timer.stop()
	api.disconnect_events()


func _on_poll_timeout() -> void:
	_refresh_active_quest(false)


func _on_websocket_state(connected: bool, _message: String, source_quest_id: String) -> void:
	if source_quest_id != active_quest_id:
		return
	if connected:
		websocket_retry_seconds = 1.0
		next_websocket_retry_msec = 0
	else:
		next_websocket_retry_msec = Time.get_ticks_msec() + int(websocket_retry_seconds * 1000.0)
		websocket_retry_seconds = minf(websocket_retry_seconds * 2.0, 30.0)


func _refresh_active_quest(manual: bool = true) -> void:
	if active_quest_id.is_empty():
		return
	api.fetch_quest(active_quest_id)
	projection_poll_ticks += 1
	if manual or projection_poll_ticks >= projection_poll_every:
		api.fetch_evidence(active_quest_id)
		api.fetch_artifacts(active_quest_id)
		projection_poll_ticks = 0
	if manual or not api.is_websocket_connected():
		api.fetch_events(active_quest_id, last_trace_sequence)
	if active_status in ["completed", "failed", "budget_exhausted"]:
		return
	if api.websocket_quest_id.is_empty() and Time.get_ticks_msec() >= next_websocket_retry_msec:
		api.connect_events(active_quest_id)


func _on_quest_received(success: bool, quest: Dictionary, message: String, source_quest_id: String) -> void:
	if source_quest_id != active_quest_id:
		return
	if not success:
		_set_backend_state(false, "轮询失败")
		_show_local_error(message)
		return
	_set_backend_state(true, "在线")
	_apply_quest(quest)


func _apply_quest(quest: Dictionary) -> void:
	var received_id := str(quest.get("id", quest.get("quest_id", active_quest_id)))
	if received_id != active_quest_id:
		return
	var received_version := int(quest.get("state_version", state_version))
	if received_version < state_version:
		return
	active_status = str(quest.get("status", "planned")).to_lower()
	state_version = received_version
	contract_version = int(quest.get("contract_version", quest.get("contract", {}).get("version", contract_version)))
	budget_usage = quest.get("budget_usage", {}) if quest.get("budget_usage", {}) is Dictionary else {}
	pending_action_id = ""
	if quest.get("pending_approval", {}) is Dictionary:
		pending_action_id = str((quest.get("pending_approval", {}) as Dictionary).get("action_id", ""))
	if quest.get("contract", {}) is Dictionary:
		contract_summary.text = _format_contract(quest.get("contract", {}) as Dictionary, budget_usage)
	var goal := str(quest.get("goal", goal_input.text.strip_edges()))
	if not goal.is_empty():
		goal_input.text = goal
	var pending_review: Dictionary = quest.get("pending_artifact_review", {}) if quest.get("pending_artifact_review", {}) is Dictionary else {}
	var is_artifact_review := active_status == "waiting_user" and not pending_review.is_empty()
	artifact_review_pending = is_artifact_review
	if is_artifact_review:
		# REST detail and artifact projections can arrive in either order after a
		# restore. Re-request the review projection once status is authoritative.
		api.fetch_artifacts(active_quest_id)
	var value := _quest_progress(quest)
	quest_id_label.text = "Quest · %s" % _short_id(active_quest_id)
	quest_id_label.tooltip_text = active_quest_id
	status_badge.text = active_status.to_upper()
	_apply_status_style(active_status)
	_update_control_button()
	_update_primary_action()
	progress_bar.value = value * 100.0
	var milestones: Array = quest.get("milestones", []) if quest.get("milestones", []) is Array else []
	_render_milestones(milestones, quest)
	town_view.set_quest_state(active_status, value, goal)
	_update_onboarding(active_status, is_artifact_review)
	if active_status == "completed":
		current_step_label.text = "所有里程碑均已完成"
	elif active_status in ["failed", "budget_exhausted"]:
		var error: Dictionary = quest.get("error", {}) if quest.get("error", {}) is Dictionary else {}
		current_step_label.text = "任务未完成：%s" % str(error.get("message", "请查看运行轨迹"))
	elif active_status == "waiting_user":
		current_step_label.text = "请预览成果并选择保留或丢弃" if is_artifact_review else "等待用户决策"
	elif active_status == "paused":
		current_step_label.text = "任务已暂停，可安全恢复"
	if active_status in ["completed", "failed", "budget_exhausted"] and not final_projection_requested:
		final_projection_requested = true
		api.fetch_evidence(active_quest_id)
		api.fetch_artifacts(active_quest_id)
	if active_status in ["completed", "failed", "budget_exhausted"]:
		_stop_automatic_updates()


func _quest_progress(quest: Dictionary) -> float:
	if quest.has("progress"):
		return clampf(float(quest["progress"]), 0.0, 1.0)
	if quest.has("progress_percent"):
		return clampf(float(quest["progress_percent"]) / 100.0, 0.0, 1.0)
	var milestones: Variant = quest.get("milestones", [])
	if milestones is Array and not milestones.is_empty():
		var completed := 0
		for milestone in milestones:
			if milestone is Dictionary and str(milestone.get("status", "")) == "completed":
				completed += 1
		return float(completed) / float(milestones.size())
	return 0.0


func _format_contract(contract: Dictionary, usage: Dictionary) -> String:
	var lines: PackedStringArray = []
	lines.append("v%d · %s" % [int(contract.get("version", 1)), "已确认" if bool(contract.get("confirmed", false)) else "待确认"])
	lines.append("目标：%s" % str(contract.get("goal", "")))
	var constraints: Array = contract.get("constraints", []) if contract.get("constraints", []) is Array else []
	var non_goals: Array = contract.get("non_goals", []) if contract.get("non_goals", []) is Array else []
	var criteria: Array = contract.get("acceptance_criteria", []) if contract.get("acceptance_criteria", []) is Array else []
	lines.append("约束 %d · 非目标 %d · 验收标准 %d" % [constraints.size(), non_goals.size(), criteria.size()])
	var budget: Dictionary = contract.get("budget", {}) if contract.get("budget", {}) is Dictionary else {}
	lines.append("预算：步骤 %d / 工具 %d / 重规划 %d" % [int(budget.get("max_steps", 0)), int(budget.get("max_tool_calls", 0)), int(budget.get("max_replans", 0))])
	lines.append("已用：步骤 %d / 工具 %d / 消息 %d / 重规划 %d" % [int(usage.get("steps", 0)), int(usage.get("tool_calls", 0)), int(usage.get("messages", 0)), int(usage.get("replans", 0))])
	return "\n".join(lines)


func _render_milestones(items: Array, quest: Dictionary) -> void:
	milestones_list.clear()
	if items.is_empty():
		milestones_list.add_item("○ 等待后端生成执行步骤")
		milestones_list.set_item_disabled(0, true)
		current_step_label.text = "正在准备任务"
		return
	var current_index := -1
	var current_step_value: Variant = quest.get("current_step", null)
	if current_step_value != null:
		current_index = maxi(int(current_step_value) - 1, 0)
	var current_milestone_id := str(quest.get("current_milestone_id", ""))
	for index in range(items.size()):
		var milestone: Dictionary = items[index] if items[index] is Dictionary else {}
		var status := str(milestone.get("status", "pending")).to_lower()
		var title := str(milestone.get("title", milestone.get("description", "步骤 %d" % (index + 1))))
		var symbol := _milestone_symbol(status)
		var dependencies: Array = milestone.get("dependencies", []) if milestone.get("dependencies", []) is Array else []
		milestones_list.add_item("%s  %d. %s  [依赖 %d]" % [symbol, index + 1, title, dependencies.size()])
		milestones_list.set_item_tooltip(index, "%s\n依赖：%s" % [str(milestone.get("description", title)), ", ".join(PackedStringArray(dependencies))])
		if status == "completed":
			milestones_list.set_item_custom_fg_color(index, Color("#4b8f67"))
		elif status == "failed":
			milestones_list.set_item_custom_fg_color(index, Color("#bb5252"))
		elif status == "running":
			milestones_list.set_item_custom_fg_color(index, Color("#c3782f"))
			current_index = index
		if not current_milestone_id.is_empty() and str(milestone.get("id", "")) == current_milestone_id:
			current_index = index
	if current_index >= 0 and current_index < items.size():
		milestones_list.select(current_index)
		milestones_list.ensure_current_is_visible()
		var current: Dictionary = items[current_index] if items[current_index] is Dictionary else {}
		current_step_label.text = "当前：%s" % str(current.get("title", current.get("description", "步骤 %d" % (current_index + 1))))


func _on_traces_received(success: bool, items: Array, message: String, source_quest_id: String) -> void:
	if source_quest_id != active_quest_id:
		return
	if not success:
		return
	if items.is_empty() and event_lines.is_empty():
		trace_log.text = "[color=#8390a1]Quest 尚未产生运行轨迹。[/color]"
		return
	for raw in items:
		if not raw is Dictionary:
			continue
		var trace := raw as Dictionary
		var sequence := int(trace.get("sequence", trace.get("id", 0)))
		if sequence > 0 and seen_event_sequences.has(sequence):
			continue
		if sequence > 0:
			seen_event_sequences[sequence] = true
		last_trace_sequence = maxi(last_trace_sequence, sequence)
		var created_at := _short_time(str(trace.get("created_at", "")))
		var event_type := str(trace.get("trace_type", trace.get("event_type", "event"))).to_upper()
		var level := str(trace.get("level", "info")).to_lower()
		var color := _trace_color(level, event_type)
		var message_text := str(trace.get("message", ""))
		if message_text.is_empty() and trace.get("payload", {}) is Dictionary:
			var payload := trace.get("payload", {}) as Dictionary
			message_text = str(payload.get("status", payload.get("milestone_id", "")))
		event_lines.append("[color=#8390a1]%s[/color] [color=%s]%s[/color]  %s" % [created_at, color, event_type, _escape_bbcode(message_text)])
	while event_lines.size() > 200:
		event_lines.remove_at(0)
	trace_log.text = "\n".join(event_lines)
	trace_log.scroll_to_line(maxi(event_lines.size() - 1, 0))

func _on_live_event(event: Dictionary, source_quest_id: String) -> void:
	_on_traces_received(true, [event], "", source_quest_id)

func _on_evidence_received(success: bool, items: Array, _message: String, source_quest_id: String) -> void:
	if source_quest_id != active_quest_id:
		return
	if not success:
		return
	last_evidence_count = items.size()
	evidence_list.clear()
	if items.is_empty():
		evidence_list.add_item("尚无验收证据")
		evidence_list.set_item_disabled(0, true)
	else:
		for raw in items:
			if not raw is Dictionary:
				continue
			var evidence := raw as Dictionary
			var mark := "✓" if bool(evidence.get("passed", false)) else "!"
			var label := str(evidence.get("artifact_path", evidence.get("criterion_id", "evidence")))
			evidence_list.add_item("%s  %s" % [mark, label])
			evidence_list.set_item_tooltip(evidence_list.item_count - 1, JSON.stringify(evidence.get("details", {})))
	trace_log.append_text("\n[color=#3f9668]Evidence: %d item(s)[/color]" % items.size())


func _on_artifacts_received(success: bool, data: Dictionary, message: String, source_quest_id: String) -> void:
	if source_quest_id != active_quest_id:
		return
	if not success:
		result_state_label.text = "成果读取失败：%s" % message
		return
	var raw_items: Variant = data.get("items", [])
	artifact_items.clear()
	if raw_items is Array:
		for raw in raw_items:
			if raw is Dictionary:
				artifact_items.append(raw as Dictionary)
	var review: Dictionary = data.get("review", {}) if data.get("review", {}) is Dictionary else {}
	result_review_key = str(review.get("review_id", ""))
	result_manifest_hash = str(review.get("manifest_hash", ""))
	var disposition := str(data.get("artifact_disposition", review.get("disposition", "pending")))
	result_select.clear()
	if artifact_items.is_empty():
		_clear_active_preview()
		result_select.add_item("尚无可预览成果")
		result_select.disabled = true
		active_artifact_id = ""
		result_preview.text = "任务完成后，Agent 生成的文本成果会直接显示在这里。"
	elif disposition == "discarded":
		_clear_active_preview()
		result_select.add_item("成果已丢弃")
		result_select.disabled = true
		active_artifact_id = ""
		result_preview.text = "已按你的选择安全删除本 Quest 的成果文件。"
	else:
		result_select.disabled = false
		var selected_index := 0
		for index in range(artifact_items.size()):
			var artifact := artifact_items[index]
			var artifact_id := str(artifact.get("artifact_id", artifact.get("id", "")))
			var artifact_path := str(artifact.get("path", artifact.get("artifact_path", "成果 %d" % (index + 1))))
			result_select.add_item(artifact_path)
			result_select.set_item_metadata(index, artifact_id)
			if artifact_id == active_artifact_id:
				selected_index = index
		result_select.select(selected_index)
		_on_result_selected(selected_index)
	var can_review := active_status == "waiting_user" and not result_review_key.is_empty() and not artifact_items.is_empty()
	keep_result_button.disabled = not can_review
	discard_result_button.disabled = not can_review
	if can_review:
		result_state_label.text = "验收已通过。请逐个查看成果，再选择保留或丢弃。"
	elif disposition == "retained":
		result_state_label.text = "✓ 成果已确认保留在 Quest 独立工作区。"
	elif disposition == "discarded":
		result_state_label.text = "成果已按用户选择安全丢弃。"
	else:
		result_state_label.text = "正在等待可预览成果…"
	_update_artifact_provenance_label()
	_update_control_button()


func _on_result_selected(index: int) -> void:
	if index < 0 or index >= result_select.item_count:
		return
	var selected_artifact_id := str(result_select.get_item_metadata(index))
	var selected_hash := ""
	if index < artifact_items.size():
		selected_hash = str(artifact_items[index].get("hash", ""))
	_update_artifact_provenance_label(index)
	if selected_artifact_id == active_artifact_id and preview_ready and not selected_hash.is_empty() and selected_hash == active_preview_hash:
		return
	active_artifact_id = selected_artifact_id
	_clear_active_preview()
	if active_artifact_id.is_empty() or active_quest_id.is_empty():
		return
	result_preview.text = "正在读取成果…"
	api.fetch_artifact_preview(active_quest_id, active_artifact_id)


func _update_artifact_provenance_label(selected_index: int = -1) -> void:
	if artifact_provenance_label == null:
		return
	var artifact: Dictionary = {}
	if selected_index >= 0 and selected_index < artifact_items.size():
		artifact = artifact_items[selected_index]
	else:
		for item in artifact_items:
			if str(item.get("artifact_id", item.get("id", ""))) == active_artifact_id:
				artifact = item
				break
	if artifact.is_empty():
		artifact_provenance_label.visible = false
		artifact_provenance_label.text = ""
		return
	var status := str(artifact.get("provenance_status", ""))
	if status.is_empty() and artifact.get("provenance", null) is Dictionary:
		status = str((artifact.get("provenance") as Dictionary).get("status", ""))
	var presentation := _provenance_presentation(status)
	artifact_provenance_label.text = str(presentation["text"])
	artifact_provenance_label.add_theme_color_override("font_color", presentation["color"] as Color)
	artifact_provenance_label.visible = true


func _provenance_presentation(status: String) -> Dictionary:
	var normalized := status.strip_edges()
	if normalized.begins_with("shadow_observed_"):
		return {"text": "审计提示：已观测到本 Quest 文件变更（兼容性影子记录）；这不是独立验证来源。", "color": COLOR_SUCCESS}
	if normalized == "shadow_existing_unchanged":
		return {"text": "审计提示：文件在本 Quest 前已存在且未变化（兼容性影子记录）。", "color": COLOR_MUTED}
	if normalized == "shadow_unobserved_created" or normalized == "shadow_external_drift":
		return {"text": "审计提示：检测到未观测或外部文件变化；请结合成果预览作出确认。", "color": COLOR_ACCENT}
	if normalized == "legacy_unobserved":
		return {"text": "审计提示：这是缺少执行基线的历史 Quest（兼容性记录）。", "color": COLOR_MUTED}
	if normalized.begins_with("unrecoverable_"):
		return {"text": "审计提示：文件审计链不完整；不影响当前成果预览和你的保留或丢弃确认。", "color": COLOR_ACCENT}
	if normalized.is_empty():
		return {"text": "审计提示：兼容 manifest（无 provenance 字段）。", "color": COLOR_MUTED}
	return {"text": "审计提示：收到未知 provenance 状态；请以成果预览和用户确认作为决定依据。", "color": COLOR_ACCENT}


func _on_artifact_preview_received(success: bool, data: Dictionary, message: String, source_quest_id: String) -> void:
	if source_quest_id != active_quest_id:
		return
	if not success:
		_clear_active_preview()
		result_preview.text = "无法预览成果：%s" % message
		return
	if str(data.get("artifact_id", data.get("id", ""))) != active_artifact_id:
		return
	active_preview_content = str(data.get("content", ""))
	active_preview_path = str(data.get("path", data.get("artifact_path", result_select.get_item_text(result_select.selected))))
	active_preview_hash = str(data.get("hash", ""))
	preview_ready = true
	artifact_preview_detail_button.disabled = false
	result_preview.text = active_preview_content


func _on_result_preview_gui_input(event: InputEvent) -> void:
	if event is InputEventMouseButton:
		var mouse_event := event as InputEventMouseButton
		if mouse_event.button_index == MOUSE_BUTTON_LEFT and mouse_event.double_click:
			_show_artifact_preview_detail()


func _show_artifact_preview_detail() -> void:
	if not preview_ready or artifact_preview_dialog == null or artifact_preview_dialog_text == null:
		return
	artifact_preview_dialog.title = "成果详情 · %s" % active_preview_path
	artifact_preview_dialog_text.text = active_preview_content
	artifact_preview_dialog_text.scroll_vertical = 0.0
	artifact_preview_dialog_text.scroll_horizontal = 0
	var available := Vector2i(maxi(320, int(size.x) - 48), maxi(260, int(size.y) - 48))
	var dialog_size := Vector2i(mini(980, available.x), mini(720, available.y))
	artifact_preview_dialog.popup_centered(dialog_size)
	call_deferred("_focus_artifact_preview_detail")


func _focus_artifact_preview_detail() -> void:
	if artifact_preview_dialog.visible and artifact_preview_dialog_text != null:
		artifact_preview_dialog_text.grab_focus()


func _on_artifact_preview_dialog_input(event: InputEvent) -> void:
	if event is InputEventKey:
		var key_event := event as InputEventKey
		if key_event.pressed and not key_event.echo and key_event.keycode == KEY_ESCAPE:
			artifact_preview_dialog.hide()


func _clear_active_preview() -> void:
	active_preview_content = ""
	active_preview_path = ""
	active_preview_hash = ""
	preview_ready = false
	if artifact_preview_detail_button != null:
		artifact_preview_detail_button.disabled = true
	if artifact_preview_dialog != null:
		artifact_preview_dialog.hide()
	if artifact_preview_dialog_text != null:
		artifact_preview_dialog_text.text = ""


func _on_keep_result_pressed() -> void:
	_submit_artifact_review("retain")


func _on_discard_result_pressed() -> void:
	discard_confirmation.popup_centered()


func _show_tutorial() -> void:
	var available := Vector2i(maxi(320, int(size.x) - 48), maxi(260, int(size.y) - 48))
	tutorial_dialog.popup_centered(Vector2i(mini(560, available.x), mini(390, available.y)))


func _build_settings_dialog() -> void:
	settings_dialog = Window.new()
	settings_dialog.name = "SettingsDialog"
	settings_dialog.title = "本地模型设置"
	settings_dialog.visible = false
	settings_dialog.transient = true
	settings_dialog.exclusive = true
	settings_dialog.close_requested.connect(_close_settings_dialog)
	add_child(settings_dialog)
	var margin := MarginContainer.new()
	margin.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	margin.add_theme_constant_override("margin_left", 16)
	margin.add_theme_constant_override("margin_top", 16)
	margin.add_theme_constant_override("margin_right", 16)
	margin.add_theme_constant_override("margin_bottom", 16)
	settings_dialog.add_child(margin)
	var column := VBoxContainer.new()
	column.add_theme_constant_override("separation", 8)
	margin.add_child(column)
	settings_provider_label = _label("OpenAI · 可配置", 14, COLOR_PARCHMENT)
	column.add_child(settings_provider_label)
	settings_provider = OptionButton.new()
	settings_provider.add_item("OpenAI")
	settings_provider.set_item_metadata(0, "openai")
	settings_provider.add_item("Qwen")
	settings_provider.set_item_metadata(1, "qwen")
	settings_provider.item_selected.connect(_on_settings_provider_selected)
	column.add_child(settings_provider)
	var unavailable := _label("DeepSeek · 当前版本尚不可用", 12, COLOR_MUTED)
	column.add_child(unavailable)
	settings_status_label = _label("正在读取本地设置…", 12, COLOR_MUTED)
	settings_status_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	column.add_child(settings_status_label)
	column.add_child(_label("Base URL", 12, COLOR_INK))
	settings_base_url = LineEdit.new()
	settings_base_url.placeholder_text = "https://…"
	settings_base_url.max_length = 256
	settings_base_url.editable = false
	settings_base_url.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	column.add_child(settings_base_url)
	column.add_child(_label("模型", 12, COLOR_INK))
	settings_model = LineEdit.new()
	settings_model.placeholder_text = "例如：qwen-plus"
	settings_model.max_length = 128
	settings_model.editable = false
	settings_model.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	column.add_child(settings_model)
	settings_model_hint = _label("仅可保存当前服务支持的模型；其他值会被本地服务拒绝。", 11, COLOR_MUTED)
	settings_model_hint.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	column.add_child(settings_model_hint)
	column.add_child(_label("API Key", 12, COLOR_INK))
	settings_api_key = LineEdit.new()
	settings_api_key.secret = true
	settings_api_key.placeholder_text = "保留现有密钥"
	settings_api_key.max_length = 4096
	settings_api_key.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	column.add_child(settings_api_key)
	settings_clear_key = CheckBox.new()
	settings_clear_key.text = "清除已配置的 API Key"
	settings_clear_key.toggled.connect(_on_settings_clear_toggled)
	column.add_child(settings_clear_key)
	var actions := HBoxContainer.new()
	actions.alignment = BoxContainer.ALIGNMENT_END
	actions.add_theme_constant_override("separation", 8)
	column.add_child(actions)
	var close_button := Button.new()
	close_button.text = "关闭"
	close_button.pressed.connect(_close_settings_dialog)
	actions.add_child(close_button)
	settings_save_button = Button.new()
	settings_save_button.text = "保存设置"
	settings_save_button.disabled = true
	_apply_button_palette(settings_save_button, COLOR_PRIMARY, COLOR_PARCHMENT, COLOR_ACCENT)
	settings_save_button.pressed.connect(_save_settings)
	actions.add_child(settings_save_button)


## Offline fixtures carry only the redacted server response and are intended for
## deterministic UI tests; API keys are never injected into the view.
func _set_settings_fixture_for_test(response: Dictionary) -> void:
	settings_fixture = response.duplicate(true)


func _close_settings_dialog() -> void:
	settings_generation += 1
	if settings_api_key != null:
		settings_api_key.clear()
		settings_api_key.editable = true
	if settings_clear_key != null:
		settings_clear_key.button_pressed = false
	if settings_base_url != null:
		settings_base_url.clear()
	if settings_model != null:
		settings_model.clear()
	if settings_dialog != null:
		settings_dialog.hide()


func _show_settings() -> void:
	if settings_dialog == null:
		return
	settings_generation += 1
	_reset_settings_view()
	var available := Vector2i(maxi(360, int(size.x) - 48), maxi(360, int(size.y) - 48))
	settings_dialog.popup_centered(Vector2i(mini(560, available.x), mini(520, available.y)))
	var context := {"generation": settings_generation, "provider": settings_selected_provider}
	if not settings_fixture.is_empty():
		call_deferred("_on_settings_received", true, _settings_fixture_for_selected_provider(), "", context)
	elif api != null:
		api.fetch_provider_settings(settings_selected_provider, context)
	else:
		_on_settings_received(false, {}, "", context)


func _on_settings_received(success: bool, data: Dictionary, _message: String, request_context: Dictionary) -> void:
	if int(request_context.get("generation", -1)) != settings_generation or settings_dialog == null or not settings_dialog.visible:
		return
	if str(request_context.get("provider", "")) != settings_selected_provider:
		return
	if not success or str(data.get("provider", "")) != settings_selected_provider:
		settings_base_url.editable = true
		settings_model.editable = true
		settings_api_key.editable = true
		settings_status_label.text = "本地设置服务或会话令牌不可用：可先填写配置，但当前无法保存。"
		return
	var models: Array = data.get("model_options", [])
	var selected_url := str(data.get("base_url", ""))
	var selected_model := str(data.get("model", ""))
	if models.is_empty() or not bool(data.get("runtime_supported", false)):
		settings_status_label.text = "此运行时尚不支持模型设置。"
		return
	if not (data.get("revision") is String):
		settings_status_label.text = "本地设置响应无效。"
		return
	settings_revision = str(data["revision"])
	if settings_revision.is_empty():
		settings_status_label.text = "本地设置响应无效。"
		return
	settings_base_url.text = selected_url
	settings_base_url.editable = true
	settings_model.text = selected_model
	settings_model.editable = true
	settings_model_hint.text = "当前服务支持：%s；其他值会被本地服务拒绝。" % ", ".join(models)
	settings_api_key.editable = not settings_clear_key.button_pressed
	settings_save_button.disabled = false
	if settings_selected_provider == "qwen":
		settings_provider_label.text = "Qwen · 可配置，真实调用待授权"
		settings_status_label.text = "已配置 API Key；真实调用待授权" if bool(data.get("api_key_configured", false)) else "可配置，真实调用待授权"
	else:
		settings_provider_label.text = "OpenAI · 可配置"
		settings_status_label.text = "已配置 API Key" if bool(data.get("api_key_configured", false)) else "尚未配置 API Key"


func _reset_settings_view() -> void:
	settings_revision = ""
	settings_api_key.clear()
	settings_api_key.editable = false
	settings_clear_key.button_pressed = false
	settings_base_url.clear()
	settings_base_url.editable = false
	settings_model.clear()
	settings_model.editable = false
	settings_model_hint.text = "正在读取当前服务支持的模型…"
	settings_save_button.disabled = true
	settings_status_label.text = "正在读取本地设置…"


func _on_settings_provider_selected(index: int) -> void:
	if settings_provider == null or index < 0:
		return
	var provider := str(settings_provider.get_item_metadata(index))
	if provider not in ["openai", "qwen"] or provider == settings_selected_provider:
		return
	settings_selected_provider = provider
	settings_generation += 1
	if settings_dialog == null or not settings_dialog.visible:
		return
	_reset_settings_view()
	var context := {"generation": settings_generation, "provider": settings_selected_provider}
	if not settings_fixture.is_empty():
		call_deferred("_on_settings_received", true, _settings_fixture_for_selected_provider(), "", context)
	elif api != null:
		api.fetch_provider_settings(settings_selected_provider, context)
	else:
		_on_settings_received(false, {}, "", context)


func _settings_fixture_for_selected_provider() -> Dictionary:
	var selected: Variant = settings_fixture.get(settings_selected_provider, settings_fixture)
	return (selected as Dictionary).duplicate(true) if selected is Dictionary else {}


func _on_settings_clear_toggled(pressed: bool) -> void:
	if settings_api_key == null:
		return
	settings_api_key.clear()
	settings_api_key.editable = not pressed


func _settings_request_body() -> Dictionary:
	if settings_revision.is_empty():
		return {}
	var base_url := settings_base_url.text.strip_edges()
	var model := settings_model.text.strip_edges()
	if base_url.is_empty() or model.is_empty():
		return {}
	var action := "keep"
	var key: Variant = null
	if settings_clear_key.button_pressed:
		action = "clear"
	elif not settings_api_key.text.strip_edges().is_empty():
		action = "replace"
		key = settings_api_key.text
	return {
		"base_url": base_url,
		"model": model,
		"api_key_action": action,
		"api_key": key,
		"expected_revision": settings_revision,
	}


func _save_settings() -> void:
	if settings_dialog == null:
		return
	var body := _settings_request_body()
	if body.is_empty():
		return
	settings_api_key.clear()
	settings_save_button.disabled = true
	settings_status_label.text = "正在保存本地设置…"
	var context := {"generation": settings_generation}
	context["provider"] = settings_selected_provider
	if api != null:
		api.save_provider_settings(settings_selected_provider, body, context)
	else:
		_on_settings_saved(false, {}, "", context)


func _on_settings_saved(success: bool, data: Dictionary, _message: String, request_context: Dictionary) -> void:
	if int(request_context.get("generation", -1)) != settings_generation or settings_dialog == null or not settings_dialog.visible:
		return
	if not success:
		settings_status_label.text = "本地设置未保存。请重新读取后再试。"
		settings_save_button.disabled = false
		return
	settings_status_label.text = "本地设置已保存。"
	if str(request_context.get("provider", "")) != settings_selected_provider or str(data.get("provider", "")) != settings_selected_provider:
		return
	if not (data.get("revision") is String) or str(data["revision"]).is_empty():
		settings_status_label.text = "本地设置响应无效。"
		settings_save_button.disabled = false
		return
	settings_revision = str(data["revision"])
	settings_save_button.disabled = false


func _apply_responsive_layout() -> void:
	if control_panel == null or town_view == null:
		return
	# The app keeps a side-by-side structure down to its supported minimum size,
	# but gives the action-heavy control panel more room on compact windows.
	var responsive_width := size.x
	var rounded_width := int(round(responsive_width))
	if rounded_width == _applied_responsive_width:
		return
	_applied_responsive_width = rounded_width
	var split := 0.675
	if responsive_width < 1100.0:
		split = 0.56
	elif responsive_width < 1380.0:
		split = 0.64
	_compact_layout = responsive_width < 1100.0

	town_view.anchor_left = 0.0
	town_view.anchor_top = 0.0
	town_view.anchor_right = split
	town_view.anchor_bottom = 1.0
	town_view.offset_left = 0.0
	town_view.offset_top = 0.0
	town_view.offset_right = 0.0
	town_view.offset_bottom = 0.0
	layout_divider.anchor_left = split
	layout_divider.anchor_right = split
	layout_divider.offset_left = -2.0
	layout_divider.offset_right = 0.0
	control_panel.anchor_left = split
	control_panel.anchor_right = 1.0
	control_panel.anchor_top = 0.0
	control_panel.anchor_bottom = 1.0
	control_panel.offset_left = 0.0
	control_panel.offset_top = 0.0
	control_panel.offset_right = 0.0
	control_panel.offset_bottom = 0.0

	var margin_size := 8 if _compact_layout else 20
	control_margin.add_theme_constant_override("margin_left", margin_size)
	control_margin.add_theme_constant_override("margin_right", margin_size)
	control_margin.add_theme_constant_override("margin_top", 8 if _compact_layout else 16)
	control_margin.add_theme_constant_override("margin_bottom", 8 if _compact_layout else 16)
	onboarding_portrait.custom_minimum_size = Vector2(44, 44) if _compact_layout else Vector2(64, 64)
	header_title.add_theme_font_size_override("font_size", 18 if _compact_layout else 24)
	header_subtitle.visible = not _compact_layout
	backend_dot.visible = not _compact_layout
	backend_label.visible = not _compact_layout
	workspace_column.custom_minimum_size.x = 82.0 if _compact_layout else 124.0
	create_button.add_theme_font_size_override("font_size", 11 if _compact_layout else 14)
	refresh_button.custom_minimum_size.x = 50.0 if _compact_layout else 70.0
	control_button.custom_minimum_size.x = 50.0 if _compact_layout else 70.0
	approve_button.text = "批" if _compact_layout else "批准"
	modify_button.text = "修改" if _compact_layout else "修改目标"
	reject_button.text = "拒绝" if _compact_layout else "拒绝任务"
	for button in [approve_button, modify_button, reject_button]:
		button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	endpoint_hint.visible = not _compact_layout


func _confirm_discard_result() -> void:
	_submit_artifact_review("discard")


func _submit_artifact_review(decision: String) -> void:
	if active_quest_id.is_empty() or result_review_key.is_empty() or result_manifest_hash.is_empty():
		return
	keep_result_button.disabled = true
	discard_result_button.disabled = true
	var key := "%s:%s" % [result_review_key, decision]
	result_state_label.text = "正在保留成果…" if decision == "retain" else "正在安全丢弃成果…"
	api.review_artifacts(
		active_quest_id,
		result_review_key,
		result_manifest_hash,
		decision,
		state_version,
		key,
		"Reviewed in ProjectTown UI"
	)


func _on_artifact_reviewed(success: bool, data: Dictionary, message: String, source_quest_id: String) -> void:
	if source_quest_id != active_quest_id:
		return
	if not success:
		result_state_label.text = "处理成果失败：%s" % message
		api.fetch_quest(active_quest_id)
		api.fetch_artifacts(active_quest_id)
		return
	if str(data.get("id", data.get("quest_id", ""))) != active_quest_id:
		return
	_apply_quest(data)
	api.fetch_artifacts(active_quest_id)
	api.fetch_evidence(active_quest_id)


func _reset_result_panel() -> void:
	if result_select == null:
		return
	result_select.clear()
	result_select.add_item("尚无成果")
	result_select.disabled = true
	_clear_active_preview()
	result_preview.text = "等待 Agent 生成并验证成果…"
	result_state_label.text = "任务完成后，成果会直接显示在这里。"
	keep_result_button.disabled = true
	discard_result_button.disabled = true
	result_review_key = ""
	result_manifest_hash = ""
	artifact_review_pending = false


func _update_onboarding(status: String, artifact_review: bool = false) -> void:
	if onboarding_step_label == null:
		return
	if artifact_review:
		onboarding_step_label.text = "第 4 步 · 预览成果并作出选择"
		onboarding_help_label.text = "逐个查看成果内容；满意就保留，不满意就安全丢弃。"
		return
	match status:
		"idle":
			onboarding_step_label.text = "第 1 步 · 描述你想完成的事情"
			onboarding_help_label.text = "选择模板或填写目标，然后创建任务草案。"
		"draft":
			onboarding_step_label.text = "第 2 步 · 审核任务草案"
			onboarding_help_label.text = "确认目标与验收标准无误后，再启动执行。"
		"planned", "running", "verifying", "replanning", "recovering":
			onboarding_step_label.text = "第 3 步 · Agent 正在执行和验收"
			onboarding_help_label.text = "你可以查看小镇状态、里程碑和运行轨迹。"
		"completed":
			onboarding_step_label.text = "已完成 · 成果已确认保留"
			onboarding_help_label.text = "可继续查看成果和 Evidence，或创建新的 Quest。"
		"failed", "budget_exhausted":
			onboarding_step_label.text = "任务未完成 · 请查看原因"
			onboarding_help_label.text = "运行轨迹会说明失败、预算耗尽或用户丢弃的原因。"
		_:
			onboarding_step_label.text = "等待下一步操作"


func _update_control_button() -> void:
	if control_button == null:
		return
	if active_status in ["running", "verifying", "replanning"]:
		control_button.text = "暂停"
		control_button.disabled = false
		control_button.tooltip_text = "安全暂停当前 Quest。"
	elif active_status in ["paused", "waiting_user", "recovering"]:
		control_button.text = "需要决策" if active_status == "waiting_user" else "恢复"
		control_button.disabled = active_status == "waiting_user"
		control_button.tooltip_text = "请在成果区或决策区完成当前选择。" if active_status == "waiting_user" else "从已保存状态继续执行。"
	else:
		control_button.text = "暂停"
		control_button.disabled = true
		control_button.tooltip_text = "只有正在执行的 Quest 可以暂停。"
	_set_decision_buttons_disabled(active_status != "waiting_user" or artifact_review_pending)


func _update_primary_action() -> void:
	if create_button == null:
		return
	if active_status == "draft":
		create_button.disabled = false
		create_button.text = "第 2 步：确认并运行"
	elif active_status in ["planned", "running", "verifying", "replanning", "recovering", "paused"]:
		create_button.disabled = true
		create_button.text = "Agent 正在执行…" if active_status != "paused" else "请先恢复当前 Quest"
	elif artifact_review_pending:
		create_button.disabled = true
		create_button.text = "请先处理成果"
	elif active_status == "waiting_user":
		create_button.disabled = true
		create_button.text = "请先完成当前决策"
	elif active_status in ["completed", "failed", "budget_exhausted"]:
		create_button.disabled = false
		create_button.text = "创建新的 Quest"


func _set_decision_buttons_disabled(disabled: bool) -> void:
	if approve_button == null:
		return
	approve_button.disabled = disabled
	modify_button.disabled = disabled
	reject_button.disabled = disabled


func _set_backend_state(online: bool, text: String) -> void:
	backend_dot.modulate = Color("#4f9b6d") if online else Color("#c65b5b")
	backend_label.text = text


func _install_pixel_theme() -> void:
	var pixel_theme := Theme.new()
	pixel_theme.default_font = PIXEL_FONT
	pixel_theme.default_font_size = 14
	pixel_theme.set_font("font", "Label", PIXEL_FONT)
	pixel_theme.set_font("font", "Button", PIXEL_FONT)
	pixel_theme.set_font("font", "LineEdit", PIXEL_FONT)
	pixel_theme.set_font("font", "TextEdit", PIXEL_FONT)
	pixel_theme.set_font("font", "OptionButton", PIXEL_FONT)
	pixel_theme.set_font("font", "ItemList", PIXEL_FONT)
	pixel_theme.set_font("normal_font", "RichTextLabel", PIXEL_FONT)
	pixel_theme.set_font("font", "ProgressBar", PIXEL_FONT)
	pixel_theme.set_font("font", "PopupMenu", PIXEL_FONT)
	pixel_theme.set_font("font", "TooltipLabel", PIXEL_FONT)
	pixel_theme.set_font("title_font", "Window", PIXEL_FONT)
	pixel_theme.set_font("font", "AcceptDialog", PIXEL_FONT)
	pixel_theme.set_font("title_font", "AcceptDialog", PIXEL_FONT)
	pixel_theme.set_font("font", "ConfirmationDialog", PIXEL_FONT)
	pixel_theme.set_font("title_font", "ConfirmationDialog", PIXEL_FONT)
	pixel_theme.set_font_size("font_size", "Button", 14)
	pixel_theme.set_color("font_color", "Button", COLOR_PARCHMENT)
	pixel_theme.set_color("font_hover_color", "Button", Color.WHITE)
	pixel_theme.set_color("font_pressed_color", "Button", Color.WHITE)
	pixel_theme.set_color("font_disabled_color", "Button", Color("#7f8b99"))
	pixel_theme.set_stylebox("normal", "Button", _pixel_frame(COLOR_STONE, COLOR_PIXEL_BLACK, COLOR_BORDER, 1, 6))
	pixel_theme.set_stylebox("hover", "Button", _pixel_frame(Color("#4a6079"), COLOR_PIXEL_BLACK, COLOR_ACCENT, 1, 6))
	pixel_theme.set_stylebox("pressed", "Button", _pixel_frame(COLOR_DEEP_STONE, COLOR_PIXEL_BLACK, COLOR_PARCHMENT, 1, 6))
	pixel_theme.set_stylebox("disabled", "Button", _pixel_frame(Color("#273248"), COLOR_PIXEL_BLACK, Color("#4c5b6d"), 1, 6))
	pixel_theme.set_stylebox("focus", "Button", _style_box(Color.TRANSPARENT, COLOR_PARCHMENT, 0, 1, 2))

	pixel_theme.set_font_size("font_size", "LineEdit", 14)
	pixel_theme.set_color("font_color", "LineEdit", COLOR_PARCHMENT)
	pixel_theme.set_color("font_placeholder_color", "LineEdit", Color("#8798a5"))
	pixel_theme.set_stylebox("normal", "LineEdit", _input_box())
	pixel_theme.set_stylebox("focus", "LineEdit", _input_box(COLOR_ACCENT))
	pixel_theme.set_stylebox("read_only", "LineEdit", _pixel_frame(Color("#1c2a3f"), COLOR_PIXEL_BLACK, COLOR_STONE, 1, 6))
	pixel_theme.set_font_size("font_size", "TextEdit", 14)
	pixel_theme.set_color("font_color", "TextEdit", COLOR_PARCHMENT)
	pixel_theme.set_color("font_placeholder_color", "TextEdit", Color("#8798a5"))
	pixel_theme.set_stylebox("normal", "TextEdit", _input_box())
	pixel_theme.set_stylebox("focus", "TextEdit", _input_box(COLOR_ACCENT))
	pixel_theme.set_stylebox("read_only", "TextEdit", _pixel_frame(Color("#162238"), COLOR_PIXEL_BLACK, COLOR_STONE, 1, 6))

	pixel_theme.set_font_size("font_size", "OptionButton", 14)
	pixel_theme.set_stylebox("normal", "OptionButton", _pixel_frame(COLOR_STONE, COLOR_PIXEL_BLACK, COLOR_BORDER, 1, 6))
	pixel_theme.set_stylebox("hover", "OptionButton", _pixel_frame(Color("#4a6079"), COLOR_PIXEL_BLACK, COLOR_ACCENT, 1, 6))
	pixel_theme.set_stylebox("pressed", "OptionButton", _pixel_frame(COLOR_DEEP_STONE, COLOR_PIXEL_BLACK, COLOR_PARCHMENT, 1, 6))
	pixel_theme.set_stylebox("disabled", "OptionButton", _pixel_frame(Color("#273248"), COLOR_PIXEL_BLACK, Color("#4c5b6d"), 1, 6))
	pixel_theme.set_color("font_color", "OptionButton", COLOR_PARCHMENT)
	pixel_theme.set_stylebox("panel", "PopupMenu", _pixel_frame(COLOR_DEEP_STONE, COLOR_PIXEL_BLACK, COLOR_ACCENT, 1, 4))
	pixel_theme.set_color("font_color", "PopupMenu", COLOR_PARCHMENT)
	pixel_theme.set_color("font_hover_color", "PopupMenu", COLOR_PARCHMENT)
	pixel_theme.set_stylebox("hover", "PopupMenu", _style_box(COLOR_WOOD, COLOR_ACCENT, 0, 1, 4))

	pixel_theme.set_stylebox("panel", "PanelContainer", _pixel_frame(COLOR_PANEL, COLOR_PIXEL_BLACK, COLOR_BORDER, 1, 4))
	pixel_theme.set_stylebox("panel", "ItemList", _pixel_frame(COLOR_DEEP_STONE, COLOR_PIXEL_BLACK, COLOR_BORDER, 1, 4))
	pixel_theme.set_color("font_color", "ItemList", COLOR_PARCHMENT)
	pixel_theme.set_color("font_selected_color", "ItemList", COLOR_PARCHMENT)
	pixel_theme.set_stylebox("selected", "ItemList", _style_box(COLOR_WOOD, COLOR_ACCENT, 0, 1, 2))
	pixel_theme.set_stylebox("separator", "HSeparator", _style_box(COLOR_BORDER, Color.TRANSPARENT, 0, 0, 0))

	pixel_theme.set_stylebox("background", "ProgressBar", _pixel_frame(COLOR_DEEP_STONE, COLOR_PIXEL_BLACK, COLOR_BORDER, 1, 2))
	pixel_theme.set_stylebox("fill", "ProgressBar", _pixel_frame(COLOR_PRIMARY, COLOR_PIXEL_BLACK, Color("#8fd3d5"), 1, 2))
	pixel_theme.set_color("font_color", "ProgressBar", COLOR_PARCHMENT)

	pixel_theme.set_constant("scrollbar_width", "VScrollBar", 10)
	pixel_theme.set_stylebox("scroll", "VScrollBar", _style_box(COLOR_DEEP_STONE, COLOR_PIXEL_BLACK, 0, 1, 0))
	pixel_theme.set_stylebox("scroll_focus", "VScrollBar", _style_box(COLOR_DEEP_STONE, COLOR_ACCENT, 0, 1, 0))
	pixel_theme.set_stylebox("grabber", "VScrollBar", _pixel_frame(COLOR_WOOD, COLOR_PIXEL_BLACK, COLOR_ACCENT, 1, 0))
	pixel_theme.set_stylebox("grabber_highlight", "VScrollBar", _pixel_frame(COLOR_ACCENT, COLOR_PIXEL_BLACK, COLOR_PARCHMENT, 1, 0))
	pixel_theme.set_stylebox("scroll", "HScrollBar", _style_box(COLOR_DEEP_STONE, COLOR_BORDER, 0, 1, 0))
	pixel_theme.set_stylebox("grabber", "HScrollBar", _style_box(COLOR_WOOD, COLOR_BORDER, 0, 1, 0))
	pixel_theme.set_stylebox("grabber_highlight", "HScrollBar", _style_box(COLOR_ACCENT, COLOR_PARCHMENT, 0, 1, 0))
	pixel_theme.set_stylebox("panel", "TooltipPanel", _style_box(COLOR_DEEP_STONE, COLOR_ACCENT, 0, 2, 5))
	pixel_theme.set_color("font_color", "TooltipLabel", COLOR_PARCHMENT)
	pixel_theme.set_stylebox("panel", "AcceptDialog", _pixel_frame(COLOR_PANEL, COLOR_PIXEL_BLACK, COLOR_ACCENT, 2, 8))
	pixel_theme.set_stylebox("panel", "ConfirmationDialog", _pixel_frame(COLOR_PANEL, COLOR_PIXEL_BLACK, COLOR_DANGER, 2, 8))
	pixel_theme.set_stylebox("embedded_border", "Window", _pixel_frame(COLOR_PANEL, COLOR_PIXEL_BLACK, COLOR_ACCENT, 2, 8))
	pixel_theme.set_stylebox("embedded_unfocused_border", "Window", _pixel_frame(COLOR_PANEL, COLOR_PIXEL_BLACK, COLOR_BORDER, 2, 8))
	pixel_theme.set_color("title_color", "Window", COLOR_PARCHMENT)
	pixel_theme.set_font_size("title_font_size", "Window", 15)
	pixel_theme.set_constant("title_height", "Window", 32)
	theme = pixel_theme


func _apply_button_palette(button: Button, fill: Color, text_color: Color, outline: Color) -> void:
	button.add_theme_color_override("font_color", text_color)
	button.add_theme_color_override("font_hover_color", Color.WHITE)
	button.add_theme_color_override("font_pressed_color", Color.WHITE)
	button.add_theme_color_override("font_disabled_color", Color("#7f8b99"))
	button.add_theme_stylebox_override("normal", _pixel_frame(fill, COLOR_PIXEL_BLACK, outline, 1, 6))
	button.add_theme_stylebox_override("hover", _pixel_frame(fill.lightened(0.12), COLOR_PIXEL_BLACK, COLOR_PARCHMENT, 1, 6))
	button.add_theme_stylebox_override("pressed", _pixel_frame(fill.darkened(0.18), COLOR_PIXEL_BLACK, COLOR_PARCHMENT, 1, 6))
	button.add_theme_stylebox_override("disabled", _pixel_frame(Color("#273248"), COLOR_PIXEL_BLACK, Color("#4c5b6d"), 1, 6))
	button.add_theme_stylebox_override("focus", _style_box(Color.TRANSPARENT, COLOR_PARCHMENT, 0, 1, 2))


func _style_dialog(dialog: AcceptDialog, accent: Color) -> void:
	dialog.add_theme_stylebox_override("panel", _pixel_frame(COLOR_PANEL, COLOR_PIXEL_BLACK, accent, 2, 8))
	dialog.add_theme_stylebox_override("embedded_border", _pixel_frame(COLOR_PANEL, COLOR_PIXEL_BLACK, accent, 2, 8))
	dialog.add_theme_stylebox_override("embedded_unfocused_border", _pixel_frame(COLOR_PANEL, COLOR_PIXEL_BLACK, COLOR_BORDER, 2, 8))
	dialog.add_theme_color_override("title_color", COLOR_PARCHMENT)
	dialog.add_theme_color_override("font_color", COLOR_PARCHMENT)
	dialog.add_theme_font_size_override("title_font_size", 15)
	dialog.add_theme_constant_override("title_height", 32)
	_apply_button_palette(dialog.get_ok_button(), accent, COLOR_PARCHMENT, COLOR_ACCENT)
	if dialog is ConfirmationDialog:
		_apply_button_palette(
			(dialog as ConfirmationDialog).get_cancel_button(),
			COLOR_STONE,
			COLOR_PARCHMENT,
			COLOR_BORDER
		)


func _apply_status_style(status: String) -> void:
	var color := _status_color(status)
	status_badge.add_theme_color_override("font_color", COLOR_PARCHMENT)
	status_badge_panel.add_theme_stylebox_override("panel", _pixel_frame(color.darkened(0.25), COLOR_PIXEL_BLACK, color.lightened(0.30), 1, 4))
	progress_bar.add_theme_stylebox_override("fill", _pixel_frame(color, COLOR_PIXEL_BLACK, color.lightened(0.26), 1, 2))
	_update_onboarding_portrait(status)


func _update_onboarding_portrait(status: String) -> void:
	if onboarding_portrait == null:
		return
	var frame := 0
	if status in ["running", "replanning", "recovering", "paused", "waiting_user"]:
		frame = 1
	elif status in ["verifying", "completed", "failed", "budget_exhausted"]:
		frame = 2
	var portrait := AtlasTexture.new()
	portrait.atlas = GUILD_COURIER_PORTRAITS
	portrait.region = Rect2(frame * 104, 0, 104, 104)
	portrait.filter_clip = true
	onboarding_portrait.texture = portrait


func _status_color(status: String) -> Color:
	match status:
		"draft", "planned":
			return Color("#507fba")
		"running", "verifying", "replanning", "recovering":
			return Color("#df8d38")
		"completed":
			return Color("#419b6c")
		"waiting_user", "paused":
			return Color("#8b6bb1")
		"failed", "budget_exhausted":
			return Color("#c65b5b")
		_:
			return Color("#8390a1")


func _milestone_symbol(status: String) -> String:
	match status:
		"completed":
			return "✓"
		"running":
			return "▶"
		"failed":
			return "!"
		_:
			return "○"


func _trace_color(level: String, event_type: String) -> String:
	if level == "error" or "FAIL" in event_type:
		return "#c65b5b"
	if level == "warning" or level == "warn":
		return "#c47b32"
	if "COMPLETE" in event_type:
		return "#3f9668"
	return "#4b78ad"


func _short_id(value: String) -> String:
	if value.length() <= 12:
		return value
	return value.left(8) + "…"


func _short_time(value: String) -> String:
	if value.is_empty():
		return "--:--:--"
	var time_part := value.get_slice("T", 1)
	if time_part.is_empty():
		return value.left(8)
	return time_part.left(8)


func _escape_bbcode(value: String) -> String:
	return value.replace("[", "［").replace("]", "］")


func _reset_create_button() -> void:
	create_button.disabled = false
	create_button.text = "下一步：创建新任务"


func _show_local_error(message: String) -> void:
	if message.is_empty():
		return
	trace_log.text = "[color=#c65b5b]%s[/color]" % _escape_bbcode(message)


func _label(text: String, font_size: int, color: Color) -> Label:
	var label := Label.new()
	label.text = text
	label.add_theme_font_size_override("font_size", font_size)
	label.add_theme_color_override("font_color", color)
	return label


func _section_title(text: String) -> HBoxContainer:
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 8)
	var label := _label(text, 14, COLOR_INK)
	row.add_child(label)
	var rule := HSeparator.new()
	rule.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(rule)
	return row


func _input_box(focus_border: Color = COLOR_BORDER) -> StyleBoxFlat:
	return _pixel_frame(COLOR_DEEP_STONE, COLOR_PIXEL_BLACK, focus_border, 1, 8)


func _pixel_frame(
	fill: Color,
	outer_border: Color,
	accent_edge: Color,
	border_width: int = 1,
	content_margin: int = 0
) -> StyleBoxFlat:
	# Reference-inspired hierarchy: a thin dark pixel frame, a lighter inner
	# highlight on top/left and a colored one-pixel shadow on bottom/right.
	var box := _style_box(fill, outer_border, 0, border_width, content_margin)
	box.shadow_color = accent_edge
	box.shadow_size = 1
	box.shadow_offset = Vector2(1, 1)
	box.expand_margin_left = 1
	box.expand_margin_top = 1
	box.expand_margin_right = 1
	box.expand_margin_bottom = 1
	return box


func _style_box(
	fill: Color,
	border: Color,
	radius: int,
	border_width: int,
	content_margin: int = 0
) -> StyleBoxFlat:
	var box := StyleBoxFlat.new()
	box.bg_color = fill
	box.border_color = border
	box.set_border_width_all(border_width)
	# All frames intentionally stay square: rounded office UI would break the
	# pixel-art language even when individual callers accidentally pass a radius.
	box.set_corner_radius_all(0)
	if content_margin > 0:
		box.content_margin_left = content_margin
		box.content_margin_right = content_margin
		box.content_margin_top = content_margin
		box.content_margin_bottom = content_margin
	return box
