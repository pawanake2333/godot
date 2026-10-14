@tool
extends EditorDebuggerPlugin

signal response_received(request_id: int, ok: bool, payload: Variant)
signal session_state_changed

var _next_request_id := 1
var _ready_sessions := {}


func _has_capture(capture: String) -> bool:
	return capture == "godot_mcp"


func _capture(message: String, data: Array, session_id: int) -> bool:
	if message == "godot_mcp:ready":
		_ready_sessions[session_id] = true
		session_state_changed.emit()
		return true
	if message != "godot_mcp:response" or data.size() < 3:
		return false
	response_received.emit(int(data[0]), bool(data[1]), data[2])
	return true


func _setup_session(session_id: int) -> void:
	var session := get_session(session_id)
	session.started.connect(_on_session_state_changed)
	session.stopped.connect(_on_session_stopped.bind(session_id))


func _on_session_state_changed() -> void:
	session_state_changed.emit()


func _on_session_stopped(session_id: int) -> void:
	_ready_sessions.erase(session_id)
	session_state_changed.emit()


func has_active_session() -> bool:
	return _get_active_session() != null


func send_runtime_request(operation: String, arguments: Dictionary) -> int:
	var session = _get_active_session()
	if session == null:
		return -1
	var request_id := _next_request_id
	_next_request_id += 1
	session.send_message("godot_mcp:request", [request_id, operation, arguments])
	return request_id


func _get_active_session():
	var sessions := get_sessions()
	for index in range(sessions.size()):
		var session = sessions[index]
		if session.is_active() and _ready_sessions.has(index):
			return session
	return null
