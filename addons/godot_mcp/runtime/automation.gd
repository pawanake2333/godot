extends RefCounted

const Animations = preload("res://addons/godot_mcp/tools/animation_tools.gd")
const Reflection = preload("res://addons/godot_mcp/contract/reflection.gd")
var animations: RefCounted
const TreeSnapshots = preload("res://addons/godot_mcp/contract/tree_snapshots.gd")
var tree_snapshots := TreeSnapshots.new()

var host: Node
var codec: RefCounted
var held := {}
var sequence_tokens := {}

func _init(runtime_host: Node, values: RefCounted) -> void:
	host = runtime_host
	codec = values
	animations = Animations.new(codec)

func ok(value: Variant) -> Dictionary:
	return {"ok": true, "value": value}

func error(code: String, message: String) -> Dictionary:
	return {"ok": false, "error": {"code": code, "message": message}}

func object_for(args: Dictionary):
	if args.has("object_ref"):
		return codec.resolve(str(args.object_ref))
	return host.get_tree().root.get_node_or_null(NodePath(str(args.get("node_path", "."))))

func invoke(operation: String, args: Dictionary) -> Dictionary:
	if operation in ["godot_animation_list", "godot_animation_inspect", "godot_animation_sample"]:
		var result: Dictionary = animations.read(operation, object_for(args), args)
		return {"ok": result.ok, "value": result.get("result"), "error": result.get("error")}
	if operation == "godot_object_call":
		var result: Dictionary = Reflection.invoke(object_for(args), args, codec)
		return {"ok": result.ok, "value": result.get("result"), "error": result.get("error")}
	match operation:
		"godot_capture_frame":
			return preload("res://addons/godot_mcp/runtime/capture.gd").capture(host, codec, args)
		"godot_monitors_read":
			var monitors: Array = []
			var names: Array = args.get("metrics", ["TIME_FPS", "TIME_PROCESS", "TIME_PHYSICS_PROCESS", "MEMORY_STATIC", "OBJECT_NODE_COUNT"])
			for name in names:
				if not ClassDB.class_has_integer_constant("Performance", str(name)) or str(name).begins_with("MONITOR_"):
					return error("UNKNOWN_MONITOR", str(name))
				var index := ClassDB.class_get_integer_constant("Performance", str(name))
				if index < 0 or index >= Performance.MONITOR_MAX:
					return error("UNKNOWN_MONITOR", str(name))
				var available := not (str(name).begins_with("RENDER_") or str(name).begins_with("PIPELINE_")) or DisplayServer.get_name() != "headless"
				var unit := "seconds" if str(name).begins_with("TIME_") or name == "AUDIO_OUTPUT_LATENCY" else "bytes" if str(name).begins_with("MEMORY_") or "MEM_USED" in str(name) else "count"
				if name == "TIME_FPS":
					unit = "frames_per_second"
				monitors.append({"name": name, "value": Performance.get_monitor(index) if available else null, "unit": unit, "available": available})
			return ok({"metrics": monitors, "process_frame": Engine.get_process_frames(), "physics_frame": Engine.get_physics_frames(), "driver": RenderingServer.get_current_rendering_driver_name()})
		"godot_object_release":
			return ok({"released": codec.release(args.object_refs)})
		"godot_object_inspect", "godot_object_set_properties":
			var object: Object = object_for(args)
			if object == null:
				return error("OBJECT_NOT_FOUND", "Runtime object reference is absent or expired")
			var infos := {}
			for info in object.get_property_list():
				if info.usage & (PROPERTY_USAGE_CATEGORY | PROPERTY_USAGE_GROUP | PROPERTY_USAGE_SUBGROUP) == 0:
					infos[str(info.name)] = info
			if operation == "godot_object_set_properties":
				var prepared := {}
				for key in args.changes:
					if not infos.has(key) or key in ["script", "owner", "name"]:
						return error("PROPERTY_NOT_FOUND", key)
					var parsed: Dictionary = codec.decode_property(args.changes[key], infos[key])
					if not parsed.ok:
						return error("INVALID_VALUE", parsed.error)
					prepared[key] = parsed.value
				for key in prepared:
					object.set(key, prepared[key])
				return ok({"applied": true, "saved": false, "undoable": false})
			var properties: Array = []
			var selected: Array = args.get("properties", [])
			for key in infos:
				if properties.size() >= int(args.get("max_properties", 128)):
					break
				if selected.is_empty() or key in selected:
					properties.append({"name": key, "type": infos[key].type, "value": codec.encode(object.get(key))})
			return ok({"object": codec.retain_object(object), "properties": properties})
		"godot_scene_tree", "godot_object_query":
			var result: Dictionary = tree_snapshots.read(host.get_tree().root, args, str(Engine.get_process_frames()), codec, true)
			return {"ok": result.ok, "value": result.get("result"), "error": result.get("error")}
		"godot_ui_focus":
			var control = object_for(args)
			if not control is Control or not control.is_visible_in_tree() or control.focus_mode == Control.FOCUS_NONE:
				return error("NOT_FOCUSABLE", "Expected visible focusable Control")
			control.grab_focus()
			return ok({"focused": control.has_focus(), "path": str(control.get_path())})
		"godot_ui_inspect":
			var base = object_for(args)
			if not base is Node:
				return error("OBJECT_NOT_FOUND", "UI root does not exist")
			var queue: Array[Node] = [base]
			var controls: Array = []
			var examined := 0
			while not queue.is_empty() and examined < 4096:
				var node := queue.pop_front() as Node
				examined += 1
				if node is Control:
					if controls.size() >= int(args.get("max_nodes", 256)):
						return ok({"controls": controls, "truncated": true})
					var rect: Rect2 = node.get_global_rect()
					var entry := {"path": str(node.get_path()), "class": node.get_class(), "object": codec.retain_object(node), "visible": node.is_visible_in_tree(), "focused": node.has_focus(), "clip_contents": node.clip_contents, "rect": codec.encode(rect)}
					if node is BaseButton:
						entry["disabled"] = node.disabled
					if node is LineEdit or node is Label or node is Button:
						entry["text"] = node.text
					controls.append(entry)
				for child in node.get_children():
					queue.append(child)
			return ok({"controls": controls, "truncated": not queue.is_empty(), "occlusion": "geometry_only"})
		"godot_input_sequence":
			var owner := str(args._input_owner)
			if sequence_tokens.has(owner):
				return error("BUSY", "Input sequence already active for this connection")
			var token := {"cancelled": false}
			sequence_tokens[owner] = token
			var deadline := Time.get_ticks_msec() + int(args.get("timeout_ms", 8000))
			var frames := 0
			for entry in args.steps:
				for frame in range(int(entry.get("after_frames", 0))):
					await host.get_tree().process_frame
					frames += 1
					if Time.get_ticks_msec() >= deadline or token.cancelled:
						release_owner(owner)
						return error("CANCELLED_OR_TIMED_OUT", "Input sequence interrupted")
				if token.cancelled or Time.get_ticks_msec() >= deadline:
					release_owner(owner)
					return error("CANCELLED_OR_TIMED_OUT", "Input sequence interrupted")
				var event_args: Dictionary = entry.event.duplicate()
				event_args["_input_owner"] = owner
				var result := send_input(event_args)
				if not result.ok:
					release_owner(owner)
					return result
			release_owner(owner)
			return ok({"completed": true, "frames": frames, "released": true})
		"godot_input_send":
			return send_input(args)
		"godot_input_release_all":
			return release_owner(str(args._input_owner))
		"godot_runtime_wait":
			var waiter := preload("res://addons/godot_mcp/runtime/waiter.gd").new()
			return await waiter.wait_for(host, codec, args)

	return error("METHOD_NOT_FOUND", operation)

func send_input(args: Dictionary) -> Dictionary:
	var owner := str(args._input_owner)
	var kind := str(args.kind)
	var event: InputEvent
	var key := ""
	var pressed := bool(args.get("pressed", true))
	match kind:
		"key":
			var keyboard := InputEventKey.new()
			keyboard.keycode = int(args.get("keycode", 0)) as Key
			keyboard.physical_keycode = int(args.get("physical_keycode", 0)) as Key
			keyboard.unicode = int(args.get("unicode", 0))
			keyboard.pressed = pressed
			keyboard.echo = bool(args.get("echo", false))
			if keyboard.keycode == 0 and keyboard.physical_keycode == 0 and keyboard.unicode == 0:
				return error("INVALID_ARGUMENT", "Key event requires a keycode, physical_keycode or unicode")
			key = "key:%d:%d" % [keyboard.keycode, keyboard.physical_keycode]
			event = keyboard
		"action_event", "action_state":
			var action := str(args.get("action", ""))
			if not InputMap.has_action(action):
				return error("ACTION_NOT_FOUND", action)
			key = kind + ":" + action
			var action_event := InputEventAction.new()
			action_event.action = action
			action_event.pressed = pressed
			action_event.strength = float(args.get("strength", 1.0))
			event = action_event
		"touch":
			var touch := InputEventScreenTouch.new()
			touch.index = int(args.get("index", 0))
			touch.position = Vector2(args.get("x", 0), args.get("y", 0))
			touch.pressed = pressed
			key = "touch:%d" % touch.index
			event = touch
		"touch_motion":
			var drag := InputEventScreenDrag.new()
			drag.index = int(args.get("index", 0))
			drag.position = Vector2(args.get("x", 0), args.get("y", 0))
			drag.relative = Vector2(args.get("delta_x", 0), args.get("delta_y", 0))
			event = drag
		"joy_button":
			var button := InputEventJoypadButton.new()
			button.device = int(args.get("device", 0))
			button.button_index = int(args.get("joy_button", 0)) as JoyButton
			button.pressed = pressed
			button.pressure = float(args.get("strength", 1.0))
			key = "joy_button:%d:%d" % [button.device, button.button_index]
			event = button
		"joy_motion":
			var axis := InputEventJoypadMotion.new()
			axis.device = int(args.get("device", 0))
			axis.axis = int(args.get("axis", 0)) as JoyAxis
			axis.axis_value = float(args.get("value", 0.0))
			pressed = not is_zero_approx(axis.axis_value)
			key = "joy_axis:%d:%d" % [axis.device, axis.axis]
			event = axis
		"text":
			for character in str(args.get("text", "")):
				var text_event := InputEventKey.new()
				text_event.unicode = character.unicode_at(0)
				text_event.pressed = true
				Input.parse_input_event(text_event)
				Input.flush_buffered_events()
				text_event = text_event.duplicate()
				text_event.pressed = false
				Input.parse_input_event(text_event)
				Input.flush_buffered_events()
			return ok({"injected": true, "characters": str(args.get("text", "")).length()})
		"wheel":
			var directions := {"up": MOUSE_BUTTON_WHEEL_UP, "down": MOUSE_BUTTON_WHEEL_DOWN, "left": MOUSE_BUTTON_WHEEL_LEFT, "right": MOUSE_BUTTON_WHEEL_RIGHT}
			if not directions.has(str(args.get("direction", ""))):
				return error("INVALID_ARGUMENT", "Invalid wheel direction")
			var wheel := InputEventMouseButton.new()
			wheel.button_index = directions[str(args.direction)]
			wheel.factor = float(args.get("factor", 1.0))
			wheel.position = Vector2(args.get("x", 0), args.get("y", 0))
			wheel.global_position = wheel.position
			wheel.shift_pressed = bool(args.get("shift", false))
			wheel.ctrl_pressed = bool(args.get("ctrl", false))
			wheel.alt_pressed = bool(args.get("alt", false))
			wheel.meta_pressed = bool(args.get("meta", false))
			wheel.pressed = true
			Input.parse_input_event(wheel)
			Input.flush_buffered_events()
			wheel = wheel.duplicate()
			wheel.pressed = false
			Input.parse_input_event(wheel)
			Input.flush_buffered_events()
			return ok({"injected": true, "kind": kind})
		"mouse_button":
			var click := InputEventMouseButton.new()
			click.button_index = int(args.get("button", 1)) as MouseButton
			click.position = Vector2(args.get("x", 0), args.get("y", 0))
			click.global_position = click.position
			click.pressed = pressed
			key = "mouse:%d" % click.button_index
			event = click
		"mouse_motion":
			var motion := InputEventMouseMotion.new()
			motion.position = Vector2(args.get("x", 0), args.get("y", 0))
			motion.global_position = motion.position
			motion.relative = Vector2(args.get("delta_x", 0), args.get("delta_y", 0))
			motion.screen_relative = motion.relative
			event = motion
		_:
			return error("INVALID_ARGUMENT", "Input kind not supported")
	if event is InputEventWithModifiers:
		event.shift_pressed = bool(args.get("shift", false))
		event.ctrl_pressed = bool(args.get("ctrl", false))
		event.alt_pressed = bool(args.get("alt", false))
		event.meta_pressed = bool(args.get("meta", false))
	if not held.has(owner):
		held[owner] = {}
	if not key.is_empty():
		if pressed:
			held[owner][key] = args.duplicate()
		else:
			held[owner].erase(key)
			for other in held:
				if other != owner and held[other].has(key):
					return ok({"injected": false, "reason": "held_by_another_client"})
	if event is InputEventMouse:
		var mask := 0
		for owner_id in held:
			for held_key in held[owner_id]:
				if str(held_key).begins_with("mouse:"):
					mask |= 1 << (int(str(held_key).trim_prefix("mouse:")) - 1)
		event.button_mask = mask
	if kind == "action_state":
		if pressed:
			Input.action_press(args.action, float(args.get("strength", 1.0)))
		else:
			Input.action_release(args.action)
	else:
		Input.parse_input_event(event)
		Input.flush_buffered_events()
	return ok({"injected": true, "kind": kind, "process_frame": Engine.get_process_frames()})

func release_owner(owner: String) -> Dictionary:
	if sequence_tokens.has(owner):
		sequence_tokens[owner].cancelled = true
		sequence_tokens.erase(owner)
	var count := 0
	for arguments in held.get(owner, {}).values().duplicate():
		arguments["pressed"] = false
		if arguments.kind == "joy_motion":
			arguments["value"] = 0.0
		send_input(arguments)
		count += 1
	held.erase(owner)
	return ok({"released": count})
