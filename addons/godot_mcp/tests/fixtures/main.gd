@tool
extends Node2D

@export var health := 100
@export var input_seen := false
@export var mouse_buttons_seen := 0
@export var drag_total := Vector2.ZERO
@export var wheel_up_seen := false
@export var ctrl_seen := false
@export var diagnostics_sent := false
@export var log_burst := 0:
	set(value):
		log_burst = value
		if Engine.is_editor_hint():
			for _index in range(value):
				push_warning("MCP ring warning")


func _process(_delta: float) -> void:
	if Engine.is_editor_hint():
		return
	if Input.is_action_pressed("test_action"):
		input_seen = true
	if health == 42 and not diagnostics_sent:
		diagnostics_sent = true
		push_warning("MCP fixture warning")
		push_error("MCP fixture error")


func _input(event: InputEvent) -> void:
	if event is InputEventMouseButton:
		if event.pressed and event.button_index in [MOUSE_BUTTON_LEFT, MOUSE_BUTTON_RIGHT]:
			mouse_buttons_seen |= 1 << (event.button_index - 1)
		if event.pressed and event.button_index == MOUSE_BUTTON_WHEEL_UP:
			wheel_up_seen = true
	if event is InputEventMouseMotion and event.button_mask & MOUSE_BUTTON_MASK_RIGHT:
		drag_total += event.relative
		ctrl_seen = event.ctrl_pressed
