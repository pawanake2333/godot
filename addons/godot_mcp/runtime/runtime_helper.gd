extends Node

const Automation = preload("res://addons/godot_mcp/runtime/automation.gd")
var _automation: RefCounted

const Values = preload("res://addons/godot_mcp/contract/values.gd")
var _values := Values.new()
var _session_id := Crypto.new().generate_random_bytes(16).hex_encode()


func _ready() -> void:
	_automation = Automation.new(self, _values)
	process_mode = Node.PROCESS_MODE_ALWAYS
	if "--hidden" in OS.get_cmdline_args():
		RenderingServer.viewport_set_update_mode(get_viewport().get_viewport_rid(), RenderingServer.VIEWPORT_UPDATE_ALWAYS)
	if EngineDebugger.is_active():
		EngineDebugger.register_message_capture("godot_mcp", _capture_message)
		EngineDebugger.send_message("godot_mcp:ready", [{"session_id": _session_id, "pid": OS.get_process_id(), "project_path": ProjectSettings.globalize_path("res://"), "stages": ["connected"], "display_mode": "headless_logic" if DisplayServer.get_name() == "headless" else "gpu", "driver": RenderingServer.get_current_rendering_driver_name()}])
		call_deferred("_announce_stages")


func _announce_stages() -> void:
	EngineDebugger.send_message("godot_mcp:stage", ["tree_ready"])
	await get_tree().physics_frame
	EngineDebugger.send_message("godot_mcp:stage", ["first_physics"])
	if DisplayServer.get_name() != "headless":
		await RenderingServer.frame_post_draw
		EngineDebugger.send_message("godot_mcp:stage", ["first_render"])

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
	if operation == "godot_runtime_stop":
		_respond(request_id, true, {"accepted": true, "session_id": _session_id})
		await get_tree().process_frame
		get_tree().call_deferred("quit")
		return
	if operation.begins_with("godot_"):
		result = await _automation.invoke(operation, arguments)
		_respond(request_id, bool(result.get("ok", false)), result.get("value") if bool(result.get("ok", false)) else result.get("error", {}))
		return
	_respond(request_id, false, {"code": "METHOD_NOT_FOUND", "message": "Unknown runtime operation: " + operation})


func _respond(request_id: int, ok: bool, payload: Variant) -> void:
	if EngineDebugger.is_active():
		EngineDebugger.send_message("godot_mcp:response", [request_id, ok, payload])
