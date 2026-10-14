extends Node

const MAX_TREE_NODES := 4096
const MAX_PROPERTIES := 256

var _mouse_button_mask := 0
var _mouse_position := Vector2.ZERO


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	_mouse_position = get_viewport().get_mouse_position()
	if EngineDebugger.is_active():
		EngineDebugger.register_message_capture("godot_mcp", _capture_message)
		EngineDebugger.send_message("godot_mcp:ready", [])


func _exit_tree() -> void:
	if EngineDebugger.is_active():
		EngineDebugger.unregister_message_capture("godot_mcp")


func _capture_message(message: String, data: Array) -> bool:
	if message != "request" or data.size() < 3:
		return false
	call_deferred("_handle_request", int(data[0]), str(data[1]), data[2])
	return true


func _handle_request(request_id: int, operation: String, arguments: Dictionary) -> void:
	var result: Dictionary
	match operation:
		"get_runtime_tree":
			result = _get_runtime_tree(arguments)
		"inspect_runtime_object":
			result = _inspect_runtime_object(arguments)
		"set_runtime_property":
			result = _set_runtime_property(arguments)
		"send_input_action":
			result = _send_input_action(arguments)
		"send_mouse_event":
			result = _send_mouse_event(arguments)
			if result.get("ok", false):
				await get_tree().process_frame
		"wait_runtime_frames":
			var frames := clampi(int(arguments.get("frames", 1)), 1, 120)
			for _frame in range(frames):
				await get_tree().process_frame
			result = _ok({"frames": frames, "process_frames": Engine.get_process_frames()})
		_:
			_respond(request_id, false, {"code": "METHOD_NOT_FOUND", "message": "Unknown runtime operation: %s" % operation})
			return
	_respond(request_id, bool(result.get("ok", false)), result.get("value", result.get("error", {})))


func _get_runtime_tree(arguments: Dictionary) -> Dictionary:
	var max_depth := clampi(int(arguments.get("max_depth", 16)), 0, 64)
	var max_nodes := clampi(int(arguments.get("max_nodes", 1024)), 1, MAX_TREE_NODES)
	var root: Node = get_tree().root
	var requested_path := str(arguments.get("root_path", ""))
	if not requested_path.is_empty():
		var requested := root.get_node_or_null(NodePath(requested_path))
		if requested == null:
			return _error("OBJECT_NOT_FOUND", "Runtime root path was not found: %s" % requested_path)
		root = requested
	var nodes: Array = []
	_append_node_tree(root, 0, max_depth, max_nodes, nodes)
	return _ok({"nodes": nodes, "truncated": nodes.size() >= max_nodes})


func _append_node_tree(node: Node, depth: int, max_depth: int, max_nodes: int, output: Array) -> void:
	if output.size() >= max_nodes:
		return
	output.append({
		"instance_id": node.get_instance_id(),
		"path": str(node.get_path()),
		"name": str(node.name),
		"type": node.get_class(),
		"depth": depth,
		"child_count": node.get_child_count(),
	})
	if depth >= max_depth:
		return
	for child in node.get_children():
		_append_node_tree(child, depth + 1, max_depth, max_nodes, output)
		if output.size() >= max_nodes:
			return


func _inspect_runtime_object(arguments: Dictionary) -> Dictionary:
	var object: Object = _resolve_object(arguments)
	if object == null:
		return _error("OBJECT_NOT_FOUND", "Runtime object was not found.")
	var requested_names: Array = arguments.get("properties", [])
	var include_all := requested_names.is_empty()
	var properties: Array = []
	for info in object.get_property_list():
		if properties.size() >= MAX_PROPERTIES:
			break
		var property_name := str(info.get("name", ""))
		var usage := int(info.get("usage", 0))
		if property_name.is_empty() or usage & (PROPERTY_USAGE_CATEGORY | PROPERTY_USAGE_GROUP | PROPERTY_USAGE_SUBGROUP):
			continue
		if not include_all and not requested_names.has(property_name):
			continue
		properties.append({
			"name": property_name,
			"type": int(info.get("type", TYPE_NIL)),
			"usage": usage,
			"value": _to_wire_value(object.get(property_name)),
		})
	return _ok({
		"instance_id": object.get_instance_id(),
		"class": object.get_class(),
		"properties": properties,
		"truncated": properties.size() >= MAX_PROPERTIES,
	})


func _set_runtime_property(arguments: Dictionary) -> Dictionary:
	var object: Object = _resolve_object(arguments)
	if object == null:
		return _error("OBJECT_NOT_FOUND", "Runtime object was not found.")
	var property_name := str(arguments.get("property", ""))
	if property_name.is_empty() or not _has_property(object, property_name):
		return _error("PROPERTY_NOT_FOUND", "Runtime property was not found: %s" % property_name)
	object.set(property_name, _from_wire_value(arguments.get("value")))
	return _ok({"property": property_name, "value": _to_wire_value(object.get(property_name))})


func _send_input_action(arguments: Dictionary) -> Dictionary:
	var action := StringName(str(arguments.get("action", "")))
	if action.is_empty() or not InputMap.has_action(action):
		return _error("ACTION_NOT_FOUND", "Input action was not found: %s" % action)
	var pressed := bool(arguments.get("pressed", true))
	var strength := clampf(float(arguments.get("strength", 1.0)), 0.0, 1.0)
	if pressed:
		Input.action_press(action, strength)
	else:
		Input.action_release(action)
	return _ok({"action": str(action), "pressed": pressed, "strength": strength})


func _resolve_object(arguments: Dictionary):
	if arguments.has("instance_id"):
		return instance_from_id(int(arguments["instance_id"]))
	var path := str(arguments.get("node_path", ""))
	if path.is_empty():
		return null
	return get_tree().root.get_node_or_null(NodePath(path))


func _send_mouse_event(arguments: Dictionary) -> Dictionary:
	var kind := str(arguments.get("kind", ""))
	var position := Vector2(float(arguments.get("x", _mouse_position.x)), float(arguments.get("y", _mouse_position.y)))
	var event: InputEventMouse
	match kind:
		"motion":
			var motion := InputEventMouseMotion.new()
			motion.position = position
			motion.global_position = position
			motion.relative = Vector2(float(arguments.get("delta_x", 0.0)), float(arguments.get("delta_y", 0.0)))
			motion.screen_relative = motion.relative
			motion.button_mask = _mouse_button_mask
			event = motion
		"button", "wheel":
			var button := int(arguments.get("button", MOUSE_BUTTON_LEFT))
			if kind == "wheel":
				var directions := {"up": MOUSE_BUTTON_WHEEL_UP, "down": MOUSE_BUTTON_WHEEL_DOWN, "left": MOUSE_BUTTON_WHEEL_LEFT, "right": MOUSE_BUTTON_WHEEL_RIGHT}
				var direction := str(arguments.get("direction", ""))
				if not directions.has(direction):
					return _error("INVALID_ARGUMENT", "Wheel direction must be up, down, left, or right.")
				button = directions[direction]
			elif button < 1 or button > 9 or button in [4, 5, 6, 7]:
				return _error("INVALID_ARGUMENT", "Mouse button must be 1, 2, 3, 8, or 9.")
			var click := InputEventMouseButton.new()
			click.button_index = button as MouseButton
			click.pressed = true if kind == "wheel" else bool(arguments.get("pressed", true))
			click.position = position
			click.global_position = position
			click.double_click = bool(arguments.get("double_click", false))
			click.factor = clampf(float(arguments.get("factor", 1.0)), 0.01, 100.0)
			if kind == "button":
				if click.pressed:
					_mouse_button_mask |= 1 << (button - 1)
				else:
					_mouse_button_mask &= ~(1 << (button - 1))
			click.button_mask = _mouse_button_mask
			event = click
		_:
			return _error("INVALID_ARGUMENT", "Mouse kind must be motion, button, or wheel.")
	event.shift_pressed = bool(arguments.get("shift", false))
	event.ctrl_pressed = bool(arguments.get("ctrl", false))
	event.alt_pressed = bool(arguments.get("alt", false))
	event.meta_pressed = bool(arguments.get("meta", false))
	_mouse_position = position
	Input.parse_input_event(event)
	Input.flush_buffered_events()
	if kind == "wheel":
		var release := event.duplicate() as InputEventMouseButton
		release.pressed = false
		Input.parse_input_event(release)
		Input.flush_buffered_events()
	return _ok({"kind": kind, "position": _to_wire_value(position), "button_mask": _mouse_button_mask})


func _has_property(object: Object, property_name: String) -> bool:
	for info in object.get_property_list():
		if str(info.get("name", "")) == property_name:
			return true
	return false


func _to_wire_value(value: Variant, depth := 0) -> Variant:
	if depth > 6:
		return {"_type": "truncated"}
	match typeof(value):
		TYPE_NIL, TYPE_BOOL, TYPE_INT, TYPE_FLOAT, TYPE_STRING:
			return value
		TYPE_STRING_NAME, TYPE_NODE_PATH:
			return str(value)
		TYPE_VECTOR2:
			return {"_type": "Vector2", "x": value.x, "y": value.y}
		TYPE_VECTOR2I:
			return {"_type": "Vector2i", "x": value.x, "y": value.y}
		TYPE_VECTOR3:
			return {"_type": "Vector3", "x": value.x, "y": value.y, "z": value.z}
		TYPE_VECTOR3I:
			return {"_type": "Vector3i", "x": value.x, "y": value.y, "z": value.z}
		TYPE_COLOR:
			return {"_type": "Color", "r": value.r, "g": value.g, "b": value.b, "a": value.a}
		TYPE_ARRAY:
			var encoded_array: Array = []
			for item in value:
				encoded_array.append(_to_wire_value(item, depth + 1))
			return encoded_array
		TYPE_DICTIONARY:
			var encoded_dictionary := {}
			for key in value:
				encoded_dictionary[str(key)] = _to_wire_value(value[key], depth + 1)
			return encoded_dictionary
		TYPE_OBJECT:
			if value == null:
				return null
			var object: Object = value
			var encoded := {"_type": object.get_class(), "instance_id": object.get_instance_id()}
			if object is Resource:
				encoded["resource_path"] = object.resource_path
			return encoded
		_:
			return {"_type": type_string(typeof(value)), "value": str(value)}


func _from_wire_value(value: Variant) -> Variant:
	if not value is Dictionary or not value.has("_type"):
		return value
	match str(value["_type"]):
		"Vector2":
			return Vector2(float(value.get("x", 0.0)), float(value.get("y", 0.0)))
		"Vector2i":
			return Vector2i(int(value.get("x", 0)), int(value.get("y", 0)))
		"Vector3":
			return Vector3(float(value.get("x", 0.0)), float(value.get("y", 0.0)), float(value.get("z", 0.0)))
		"Vector3i":
			return Vector3i(int(value.get("x", 0)), int(value.get("y", 0)), int(value.get("z", 0)))
		"Color":
			return Color(float(value.get("r", 0.0)), float(value.get("g", 0.0)), float(value.get("b", 0.0)), float(value.get("a", 1.0)))
		_:
			return value.get("value")


func _respond(request_id: int, ok: bool, payload: Variant) -> void:
	if EngineDebugger.is_active():
		EngineDebugger.send_message("godot_mcp:response", [request_id, ok, payload])


func _ok(value: Variant) -> Dictionary:
	return {"ok": true, "value": value}


func _error(code: String, message: String) -> Dictionary:
	return {"ok": false, "error": {"code": code, "message": message}}
