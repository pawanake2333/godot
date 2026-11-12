extends RefCounted

signal changed
var expired := false
var frames := 0
var fired := false
var invalidated := false

func on_timeout() -> void:
	expired = true
	changed.emit()

func on_frame() -> void:
	frames += 1
	changed.emit()

func on_signal() -> void:
	fired = true
	changed.emit()

func on_invalidated() -> void:
	invalidated = true
	changed.emit()

func wait_for(host: Node, codec: RefCounted, args: Dictionary) -> Dictionary:
	var tree := host.get_tree()
	var kind := str(args.get("condition", "frames"))
	var clock := str(args.get("clock", "process"))
	var source: Object
	var signal_callback: Callable
	var property_name := str(args.get("property", ""))
	var expected: Variant
	if kind != "frames":
		source = codec.resolve(str(args.object_ref)) if args.has("object_ref") else tree.root.get_node_or_null(NodePath(str(args.get("node_path", ""))))
		if source == null:
			return failure("OBJECT_NOT_FOUND", "Wait target is absent or expired")
		if kind == "property":
			var found := false
			for info in source.get_property_list():
				if str(info.name) == property_name:
					found = true
			if not found:
				return failure("PROPERTY_NOT_FOUND", property_name)
			var decoded: Dictionary = codec.decode(args.get("value"))
			if not decoded.ok:
				return failure("INVALID_VALUE", decoded.error)
			expected = decoded.value
		else:
			var signal_name := str(args.get("signal_name", ""))
			if not source.has_signal(signal_name):
				return failure("SIGNAL_NOT_FOUND", signal_name)
			var count := 0
			for info in source.get_signal_list():
				if str(info.name) == signal_name:
					count = info.args.size()
			signal_callback = on_signal.unbind(count)
			source.connect(signal_name, signal_callback)
		if source is Node:
			source.tree_exiting.connect(on_invalidated)
	var source_ref := weakref(source) if source != null else null
	# Do not keep an ordinary object alive while waiting for it.
	source = null
	var frame_signal: Signal = tree.physics_frame if kind == "frames" and clock == "physics" else tree.process_frame
	frame_signal.connect(on_frame)
	var timer := tree.create_timer(float(args.get("timeout_ms", 5000)) / 1000.0, true, false, true)
	timer.timeout.connect(on_timeout)
	var result: Dictionary
	while true:
		if expired:
			result = failure("TIMEOUT", "Runtime condition deadline reached")
			break
		if kind == "frames" and clock == "physics" and tree.paused:
			result = failure("PAUSED", "Physics simulation is paused")
			break
		var actual: Variant
		var reached := false
		if kind == "frames":
			reached = frames >= int(args.get("frames", 1))
		else:
			source = source_ref.get_ref()
			if source == null or invalidated:
				result = failure("OBJECT_EXPIRED", "Wait target left the tree or was freed")
				break
			if kind == "signal":
				reached = fired
			else:
				actual = source.get(property_name)
				var comparison := str(args.get("comparison", "eq"))
				if comparison in ["lt", "le", "gt", "ge"] and not ((actual is int or actual is float) and (expected is int or expected is float)):
					result = failure("INVALID_COMPARISON", "Ordering comparisons require numeric values")
					break
				match comparison:
					"eq": reached = actual == expected
					"ne": reached = actual != expected
					"lt": reached = actual < expected
					"le": reached = actual <= expected
					"gt": reached = actual > expected
					"ge": reached = actual >= expected
		if reached:
			result = {"ok": true, "value": {"condition": kind, "satisfied": true, "frames": frames, "clock": clock, "process_frames": Engine.get_process_frames(), "physics_frames": Engine.get_physics_frames(), "paused": tree.paused, "actual": codec.encode(actual)}}
			break
		source = null
		await changed
	frame_signal.disconnect(on_frame)
	if timer.timeout.is_connected(on_timeout):
		timer.timeout.disconnect(on_timeout)
	if source_ref != null:
		source = source_ref.get_ref()
		if source != null:
			if signal_callback.is_valid() and source.is_connected(str(args.get("signal_name", "")), signal_callback):
				source.disconnect(str(args.signal_name), signal_callback)
			if source is Node and source.tree_exiting.is_connected(on_invalidated):
				source.tree_exiting.disconnect(on_invalidated)
	return result

func failure(code: String, message: String) -> Dictionary:
	var tree := Engine.get_main_loop() as SceneTree
	return {"ok": false, "error": {"code": code, "message": message, "scene_tree_paused": tree.paused if tree != null else false}}
