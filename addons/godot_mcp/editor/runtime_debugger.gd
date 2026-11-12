@tool
extends EditorDebuggerPlugin

signal response_received(request_id: int, ok: bool, payload: Variant)
signal session_state_changed

var _next_request_id := 1
var _ready_sessions := {}
var _request_sessions := {}
var _debug_states := {}
const Values = preload("res://addons/godot_mcp/contract/values.gd")
var _debug_values := Values.new()


func _has_capture(capture: String) -> bool:
	return capture == "godot_mcp"


func _capture(message: String, data: Array, session_id: int) -> bool:
	if message == "godot_mcp:ready":
		_ready_sessions[session_id] = data[0] if not data.is_empty() and data[0] is Dictionary else {"session_id": str(session_id)}
		session_state_changed.emit()
		return true
	if message == "godot_mcp:stage" and _ready_sessions.has(session_id):
		if not data.is_empty() and str(data[0]) not in _ready_sessions[session_id].stages:
			_ready_sessions[session_id].stages.append(str(data[0]))
			session_state_changed.emit()
		return true
	if message != "godot_mcp:response" or data.size() < 3:
		return false
	var request_id := int(data[0])
	if _request_sessions.get(request_id, -1) != session_id:
		return true
	_request_sessions.erase(request_id)
	response_received.emit(request_id, bool(data[1]), data[2])
	return true


func _setup_session(session_id: int) -> void:
	var session := get_session(session_id)
	_debug_states[session_id] = {"stack": [], "variables": [], "variables_expected": -1, "revision": 0, "reason": ""}
	session.started.connect(_on_debug_started.bind(session_id))
	session.breaked.connect(_on_debug_breaked.bind(session_id))
	session.continued.connect(_on_session_state_changed)
	if session.has_signal("stack_dump"):
		session.stack_dump.connect(_on_debug_stack.bind(session_id))
		session.break_reason.connect(_on_debug_reason.bind(session_id))
		session.stack_frame_variables_started.connect(_on_debug_variables_started.bind(session_id))
		session.stack_frame_variable.connect(_on_debug_variable.bind(session_id))
		if session.has_signal("profiler_frame"):
			session.profiler_frame.connect(_on_profiler_frame.bind(session_id))
	session.stopped.connect(_on_session_stopped.bind(session_id))


func _on_debug_started(_index: int) -> void:
	_on_session_state_changed()

func _on_debug_breaked(_can_debug: bool, index: int) -> void:
	_debug_states[index].variables_expected = -1
	_debug_states[index].stack = []
	_debug_states[index].variables = []
	_on_session_state_changed()

func _on_debug_stack(frames: Array, index: int) -> void:
	_debug_states[index].stack = frames.slice(0, 256)
	_debug_states[index].revision += 1
	_on_session_state_changed()

func _on_debug_reason(reason: String, index: int) -> void:
	_debug_states[index].reason = reason

func _on_debug_variables_started(count: int, index: int) -> void:
	_debug_states[index].variables = []
	_debug_states[index].variables_expected = count
	_on_session_state_changed()

func _on_debug_variable(data: Array, index: int) -> void:
	if data.size() == 5 and _debug_states[index].variables.size() < 2048:
		_debug_states[index].variables.append({"name": data[0], "scope": data[1], "type": data[2], "value": _debug_values.encode(data[3]), "type_hint": data[4]})
	_on_session_state_changed()

func _expire(state: Dictionary) -> void:
	state.expired = true
	session_state_changed.emit()

func _on_session_state_changed() -> void:
	session_state_changed.emit()


func forget_request(request_id: int) -> void:
	_request_sessions.erase(request_id)

func _on_session_stopped(session_id: int) -> void:
	for request_id in _request_sessions.keys():
		if _request_sessions[request_id] == session_id:
			_request_sessions.erase(request_id)
			response_received.emit(int(request_id), false, {"code": "SESSION_DISCONNECTED", "message": "The selected runtime disconnected before replying"})
	_ready_sessions.erase(session_id)
	session_state_changed.emit()


func has_active_session() -> bool:
	return _get_active_session() != null


func send_runtime_request(operation: String, arguments: Dictionary) -> int:
	var session = _get_active_session(str(arguments.get("session_id", "")))
	if session == null:
		return -1
	var request_id := _next_request_id
	_next_request_id += 1
	for index in range(get_sessions().size()):
		if get_session(index) == session:
			_request_sessions[request_id] = index
	session.send_message("godot_mcp:request", [request_id, operation, arguments])
	return request_id


func list_sessions() -> Array:
	var result: Array = []
	for index in _ready_sessions:
		if get_session(index).is_active():
			var entry: Dictionary = _ready_sessions[index].duplicate()
			entry["debugger_index"] = index
			entry["ready"] = true
			result.append(entry)
	return result

func _get_active_session(target := ""):
	var sessions := get_sessions()
	for index in range(sessions.size()):
		var session = sessions[index]
		if session.is_active() and _ready_sessions.has(index) and (target.is_empty() or str(_ready_sessions[index].get("session_id", "")) == target):
			return session
	return null

func wait_sessions(minimum: int, timeout_ms: int) -> Dictionary:
	var state := {"expired": false}
	var timer := EditorInterface.get_base_control().get_tree().create_timer(float(timeout_ms) / 1000.0)
	timer.timeout.connect(_expire.bind(state))
	while list_sessions().size() < minimum:
		if state.expired:
			return {"ok": false, "error": {"code": "TIMEOUT", "message": "Debug sessions did not become ready"}}
		await session_state_changed
	return {"ok": true, "result": {"sessions": list_sessions()}}

func wait_stage(args: Dictionary) -> Dictionary:
	var state := {"expired": false}
	var timer := EditorInterface.get_base_control().get_tree().create_timer(float(args.get("timeout_ms", 5000)) / 1000.0)
	timer.timeout.connect(_expire.bind(state))
	while true:
		for entry in list_sessions():
			if (args.has("pid") and int(entry.get("pid", -1)) != int(args.pid)) or (args.has("session_id") and entry.session_id != args.session_id):
				continue
			var stage := str(args.get("stage", "tree_ready"))
			if stage == "first_render" and entry.get("display_mode") == "headless_logic":
				return {"ok": false, "error": {"code": "UNSUPPORTED_DISPLAY", "message": "Dummy rendering cannot produce first_render"}}
			if stage in entry.get("stages", []):
				return {"ok": true, "result": {"session": entry, "stage": stage}}
		if state.expired:
			return {"ok": false, "error": {"code": "TIMEOUT", "message": "Runtime stage deadline reached"}}
		await session_state_changed
	return {"ok": false, "error": {"code": "TIMEOUT", "message": "Runtime stage deadline reached"}}

func debug_invoke(operation: String, args: Dictionary) -> Dictionary:
	if operation in ["godot_debugger_breakpoint_set", "godot_debugger_breakpoint_clear"]:
		var path := str(args.path)
		var line := int(args.line)
		var source := FileAccess.get_file_as_string(path) if FileAccess.file_exists(path) else ""
		for document in EditorInterface.get_script_editor().get_open_script_editors():
			var resource: Resource = document.call("get_edited_resource")
			if resource != null and resource.resource_path == path and document.get_base_editor() is CodeEdit:
				source = document.get_base_editor().text
		if not path.begins_with("res://") or not FileAccess.file_exists(path) or not ResourceLoader.exists(path, "Script") or line < 1 or line > source.split("\n").size():
			return {"ok": false, "error": {"code": "INVALID_BREAKPOINT", "message": "Select an existing project script and one-based line"}}
		var key := "%s:%d" % [path, line]
		var enabled := operation == "godot_debugger_breakpoint_set"
		# ScriptEditor owns gutter markers, closed-buffer cache and the native
		# debugger registry. A private replay list would resurrect human deletions.
		EditorInterface.get_script_editor().call("set_breakpoint", load(path), line - 1, enabled)
		return {"ok": true, "result": {"breakpoint_id": key, "registered": enabled, "scope": "editor_sessions"}}
	var session = _get_active_session(str(args.session_id))
	if session == null:
		return {"ok": false, "error": {"code": "SESSION_NOT_FOUND", "message": "Session is no longer active"}}
	var index := get_sessions().find(session)
	var state: Dictionary = _debug_states[index]
	var expiration := {"expired": false}
	var timer := EditorInterface.get_base_control().get_tree().create_timer(float(args.get("timeout_ms", 5000)) / 1000.0)
	timer.timeout.connect(_expire.bind(expiration))
	if operation == "godot_debugger_control":
		var revision := int(state.revision)
		var action := str(args.action)
		var result: int = session.call("debug_control", action)
		if result != OK:
			return {"ok": false, "error": {"code": "INVALID_DEBUG_STATE", "message": error_string(result)}}
		while session.is_active() and not expiration.expired:
			if (action == "continue" and not session.is_breaked()) or (action != "continue" and session.is_breaked() and int(state.revision) > revision):
				break
			await session_state_changed
	else:
		if args.get("wait_paused", false):
			while session.is_active() and not expiration.expired and (not session.is_breaked() or state.stack.is_empty()):
				await session_state_changed
		if session.is_breaked() and args.has("frame"):
			state.variables_expected = -1
			if not session.call("request_stack_variables", int(args.frame)):
				return {"ok": false, "error": {"code": "INVALID_FRAME", "message": "Cannot request frame variables"}}
			while not expiration.expired and session.is_active() and session.is_breaked() and (state.variables_expected < 0 or state.variables.size() < mini(int(state.variables_expected), 2048)):
				await session_state_changed
	if expiration.expired:
		return {"ok": false, "error": {"code": "TIMEOUT", "message": "Debugger response deadline reached"}}
	return {"ok": true, "result": {"session_id": args.session_id, "active": session.is_active(), "paused": session.is_breaked(), "reason": state.reason, "stack": state.stack if session.is_breaked() else [], "variables": state.variables.slice(0, int(args.get("max_variables", 128))) if session.is_breaked() else [], "variables_truncated": state.variables.size() > int(args.get("max_variables", 128)), "revision": state.revision}}

func _on_profiler_frame(kind: String, data: Array, index: int) -> void:
	var capture: Dictionary = _debug_states[index].get("capture", {})
	if not capture.is_empty() and capture.kind == "servers" and kind == "servers_signature":
		if data.size() >= 2 and capture.signatures.size() < 4096:
			capture.signatures[str(data[1])] = data[0]
		return
	if capture.is_empty() or capture.kind != kind or capture.frames.size() >= capture.limit:
		return
	var frame := {}
	if kind == "visual" and data.size() >= 2:
		frame = {"frame": data[0], "areas": []}
		for offset in range(2, mini(data.size(), 2 + 128 * 3), 3):
			if offset + 2 < data.size():
				frame.areas.append({"name": data[offset], "cpu_ms": data[offset + 1], "gpu_ms": data[offset + 2]})
	elif kind == "servers" and data.size() >= 8:
		frame = {"frame": data[0], "frame_seconds": data[1], "process_seconds": data[2], "physics_seconds": data[3], "physics_frame_seconds": data[4], "script_seconds": data[5], "servers": [], "functions": []}
		var offset := 7
		for server in range(int(data[6])):
			if offset + 1 >= data.size():
				return
			var name = data[offset]
			var size := int(data[offset + 1])
			offset += 2
			var functions: Array = []
			for item in range(0, size, 2):
				if offset + item + 1 >= data.size():
					return
				if functions.size() < 64:
					functions.append({"name": data[offset + item], "seconds": data[offset + item + 1]})
			frame.servers.append({"name": name, "functions": functions})
			offset += size
		if offset < data.size():
			offset += 1
			while offset + 4 < data.size() and frame.functions.size() < 64:
				frame.functions.append({"signature_id": data[offset], "calls": data[offset + 1], "self_seconds": data[offset + 2], "total_seconds": data[offset + 3], "internal_seconds": data[offset + 4]})
				offset += 5
	if not frame.is_empty():
		capture.frames.append(frame)
		session_state_changed.emit()

func profiler_capture(args: Dictionary) -> Dictionary:
	var session = _get_active_session(str(args.session_id))
	if session == null:
		return {"ok": false, "error": {"code": "SESSION_NOT_FOUND", "message": "Session no longer active"}}
	if not session.has_signal("profiler_frame") or session.is_breaked():
		return {"ok": false, "error": {"code": "UNAVAILABLE", "message": "Profiler requires the custom engine and a running unpaused session"}}
	var index := get_sessions().find(session)
	var kind := str(args.get("kind", "servers"))
	if kind == "visual" and _ready_sessions[index].get("display_mode") == "headless_logic":
		return {"ok": false, "error": {"code": "UNSUPPORTED_DISPLAY", "message": "Visual profiler requires a GPU renderer"}}
	if _debug_states[index].has("capture"):
		return {"ok": false, "error": {"code": "BUSY", "message": "A profiler capture already owns this session"}}
	var capture := {"signatures": {}, "kind": kind, "frames": [], "limit": int(args.get("frames", 8)), "expired": false}
	_debug_states[index].capture = capture
	var timer := EditorInterface.get_base_control().get_tree().create_timer(float(args.get("timeout_ms", 5000)) / 1000.0)
	timer.timeout.connect(_expire.bind(capture))
	var previous: Dictionary = session.get_profiler_state(kind)
	var owned := not bool(previous.enabled)
	if owned:
		session.toggle_profiler(kind, true, [[64, false]] if kind == "servers" else [])
	var generation: int = int(session.get_profiler_state(kind).generation)
	while session.is_active() and not capture.expired and capture.frames.size() < capture.limit:
		await session_state_changed
	if session.is_active() and owned and int(session.get_profiler_state(kind).generation) == generation:
		session.toggle_profiler(kind, false)
	_debug_states[index].erase("capture")
	if capture.frames.is_empty():
		return {"ok": false, "error": {"code": "NO_PROFILE_DATA", "message": "No profiler frames arrived before deadline or disconnect"}}
	return {"ok": true, "result": {"session_id": args.session_id, "kind": kind, "signatures": capture.signatures, "frames": capture.frames, "complete": capture.frames.size() == capture.limit, "timed_out": capture.expired, "source": "engine_profiler", "started_by_mcp": owned, "profiler_state_after": session.get_profiler_state(kind) if session.is_active() else {}}}
