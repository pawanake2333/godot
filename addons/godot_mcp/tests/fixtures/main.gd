@tool
extends Node2D

signal automation_pulse(frame: int)

@export var output_probe := 0:
	set(value):
		output_probe = value
		if Engine.is_editor_hint():
			emit_output_probe()
var native_inspector_events: Array = []
var native_wrong_inspector_events := 0
var native_restart_requests := 0
var native_output_events := {"collapse": 0, "search": 0, "filters": 0, "clear": 0}
var native_ui_done := 0
var last_native_ui_command := 0
@export var native_ui_command := 0:
	set(value):
		native_ui_command = value
		if Engine.is_editor_hint() and value > last_native_ui_command:
			last_native_ui_command = value
			call_deferred("native_ui_action", value)
var native_output_widgets: Dictionary:
	get:
		if not Engine.is_editor_hint():
			return {}
		var base := EditorInterface.get_base_control()
		var levels: Array = []
		for entry in [["Std", "info"], ["Error", "error"], ["Warning", "warning"], ["Editor", "editor"]]:
			var button := base.find_child("ToggleOutput" + entry[0], true, false) as Button
			if button != null and button.button_pressed:
				levels.append(entry[1])
		return {"collapse": base.find_child("CollapseOutputMessages", true, false).button_pressed, "search": base.find_child("FilterOutputMessages", true, false).text, "levels": levels}
@export var target_path := NodePath("Marker")
var last_human_probe := 0
@export var human_probe := 0:
	set(value):
		human_probe = value
		if Engine.is_editor_hint() and value > last_human_probe:
			last_human_probe = value
			call_deferred("human_editor_action", value)
var editor_breakpoints: PackedStringArray:
	get:
		return EditorInterface.get_script_editor().get_breakpoints() if Engine.is_editor_hint() else PackedStringArray()
@export var human_probe_done := 0
@export var human_profiler_auto := false:
	set(value):
		human_profiler_auto = value
		if Engine.is_editor_hint():
			EditorInterface.get_editor_settings().set_project_metadata("debug_options", "autostart_profiler", value)
@export var user_data := {}
@export var health := 100
@export var input_seen := false
@export var mouse_buttons_seen := 0
@export var drag_total := Vector2.ZERO
@export var wheel_up_seen := false
@export var ctrl_seen := false
@export var diagnostics_sent := false
@export var touch_pressed := false
@export var touch_drag_seen := false
@export var joy_pressed := false
@export var joy_axis := 0.0
@export var typed_key_seen := false
@export var typed_key_pressed := false
@export var action_event_seen := false
@export var action_pressed_now := false
@export var log_burst := 0:
	set(value):
		log_burst = value
		if Engine.is_editor_hint():
			for _index in range(value):
				push_warning("MCP ring warning")


func _process(_delta: float) -> void:
	if Engine.is_editor_hint():
		return
	automation_pulse.emit(Engine.get_process_frames())
	debug_probe()
	action_pressed_now = Input.is_action_pressed("test_action")
	if Input.is_action_pressed("test_action"):
		input_seen = true
	if health == 42 and not diagnostics_sent:
		diagnostics_sent = true
		push_warning("MCP fixture warning")
		push_error("MCP fixture error")


func _input(event: InputEvent) -> void:
	if event is InputEventScreenTouch:
		touch_pressed = event.pressed
	if event is InputEventScreenDrag:
		touch_drag_seen = true
	if event is InputEventJoypadButton:
		joy_pressed = event.pressed
	if event is InputEventJoypadMotion:
		joy_axis = event.axis_value
	if event is InputEventKey:
		typed_key_seen = true
		typed_key_pressed = event.pressed
	if event is InputEventAction:
		action_event_seen = true
	if event is InputEventMouseButton:
		if event.pressed and event.button_index in [MOUSE_BUTTON_LEFT, MOUSE_BUTTON_RIGHT]:
			mouse_buttons_seen |= 1 << (event.button_index - 1)
		if event.pressed and event.button_index == MOUSE_BUTTON_WHEEL_UP:
			wheel_up_seen = true
	if event is InputEventMouseMotion and event.button_mask & MOUSE_BUTTON_MASK_RIGHT:
		drag_total += event.relative
		ctrl_seen = event.ctrl_pressed

func debug_probe() -> int:
	var probe_local := health + 1
	probe_local += 1
	return probe_local

func create_ephemeral() -> void:
	var node := Node.new()
	node.name = "WaitTarget"
	add_child(node)
	var timer := Timer.new()
	node.add_child(timer)
	timer.one_shot = true
	timer.timeout.connect(node.queue_free)
	timer.start(0.25)


func emit_output_probe() -> void:
	print("MCP output marker Ω")
	print("MCP output duplicate")
	print("MCP output duplicate")
	print_rich("[b]MCP output rich[/b]")
	push_warning("MCP output warning")
	push_error("MCP fixture error")

func human_editor_action(action: int) -> void:
	var undo := EditorInterface.get_editor_undo_redo()
	if action == 1:
		# A real EditorProperty signal enters the same UI handler as a user edit.
		EditorInterface.inspect_object(self, "", true)
		for property in EditorInterface.get_inspector().find_children("*", "EditorProperty", true, false):
			if property.get_edited_object() == self and str(property.get_edited_property()) == "health":
				property.emit_changed("health", 117)
				break
	elif action == 2:
		undo.undo()
		undo.redo()
	elif action == 3:
		for point in EditorInterface.get_script_editor().get_breakpoints():
			var separator := str(point).rfind(":")
			EditorInterface.get_script_editor().set_breakpoint(load(str(point).left(separator)), int(str(point).substr(separator + 1)) - 1, false)
	human_probe_done = action

func _native_property_edited(property: String) -> void:
	if property == "health":
		if EditorInterface.get_inspector().get_edited_object() == self:
			native_inspector_events.append(health)
		else:
			native_wrong_inspector_events += 1

func _native_output_event(_value: Variant, kind: String) -> void:
	native_output_events[kind] += 1

func native_ui_action(command: int) -> void:
	var base := EditorInterface.get_base_control()
	if command == 1:
		EditorInterface.inspect_object(self, "", true)
		EditorInterface.get_inspector().property_edited.connect(_native_property_edited)
		EditorInterface.get_inspector().restart_requested.connect(func(): native_restart_requests += 1)
		for kind in ["Std", "Error", "Warning", "Editor"]:
			base.find_child("ToggleOutput" + kind, true, false).toggled.connect(_native_output_event.bind("filters"))
		base.find_child("CollapseOutputMessages", true, false).toggled.connect(_native_output_event.bind("collapse"))
		base.find_child("FilterOutputMessages", true, false).text_changed.connect(_native_output_event.bind("search"))
		base.find_child("ClearOutput", true, false).pressed.connect(_native_output_event.bind(null, "clear"))
	elif command == 3:
		# Human UI events use the actual native controls, not the MCP entry point.
		base.find_child("ToggleOutputStd", true, false).button_pressed = true
		for kind in ["Error", "Warning", "Editor"]:
			base.find_child("ToggleOutput" + kind, true, false).button_pressed = false
		var collapse_button := base.find_child("CollapseOutputMessages", true, false) as Button
		collapse_button.button_pressed = not collapse_button.button_pressed
		var search := base.find_child("FilterOutputMessages", true, false) as LineEdit
		search.text = "human Output search"
		search.text_changed.emit(search.text)
		base.find_child("ClearOutput", true, false).pressed.emit()
	elif command == 2:
		EditorInterface.inspect_object(get_node("Background"), "", true)
		EditorInterface.get_editor_undo_redo().undo() # Undo this command's setter.
		EditorInterface.get_editor_undo_redo().undo() # Undo health with another Inspector target.
	native_ui_done = command

func _validate_property(property: Dictionary) -> void:
	if str(property.name) == "health":
		property.usage |= PROPERTY_USAGE_RESTART_IF_CHANGED
	# Command probes are editable test inputs, not scene data to replay on reopen.
	if str(property.name) in ["native_ui_command", "human_probe", "output_probe", "log_burst", "human_profiler_auto"]:
		property.usage = PROPERTY_USAGE_EDITOR | PROPERTY_USAGE_SCRIPT_VARIABLE
