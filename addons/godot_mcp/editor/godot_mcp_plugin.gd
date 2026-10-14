@tool
extends EditorPlugin

const RuntimeDebugger = preload("res://addons/godot_mcp/editor/runtime_debugger.gd")
const RUNTIME_AUTOLOAD_NAME := "GodotMCPRuntime"
const RUNTIME_AUTOLOAD_PATH := "res://addons/godot_mcp/runtime/runtime_helper.gd"
const LISTEN_HOST := "127.0.0.1"
const ENDPOINT_PATH := "res://.godot/mcp/endpoint.json"
const REQUEST_TIMEOUT_MSEC := 10000
const COMMAND_ALLOWLIST := {
	"editor/save_scene": true,
	"editor/run_project": true,
	"editor/run_current_scene": true,
	"editor/editor_2d": true,
	"editor/editor_3d": true,
	"editor/editor_script": true,
	"editor/editor_game": true,
}

var _server := TCPServer.new()
var _clients: Array[Dictionary] = []
var _requests := {}
var _next_rpc_id := 1
var _token := ""
var _endpoint_owned := false
var _polling := false
var _runtime_debugger: EditorDebuggerPlugin
var _pending_runtime := {}


func _enter_tree() -> void:
	if FileAccess.file_exists(ENDPOINT_PATH):
		var existing = JSON.parse_string(FileAccess.get_file_as_string(ENDPOINT_PATH))
		if existing is Dictionary and OS.is_process_running(int(existing.get("pid", -1))):
			push_error("Godot MCP already has an editor for this project.")
			set_process(false)
			return
	_ensure_runtime_autoload()
	_runtime_debugger = RuntimeDebugger.new()
	_runtime_debugger.response_received.connect(_on_runtime_response)
	_runtime_debugger.session_state_changed.connect(_on_runtime_state_changed)
	add_debugger_plugin(_runtime_debugger)
	_token = Crypto.new().generate_random_bytes(32).hex_encode()
	var error := _server.listen(0, LISTEN_HOST)
	if error != OK:
		push_error("Godot MCP could not listen: %s" % error_string(error))
		set_process(false)
		return
	DirAccess.make_dir_recursive_absolute(ProjectSettings.globalize_path(ENDPOINT_PATH.get_base_dir()))
	var endpoint := FileAccess.open(ENDPOINT_PATH, FileAccess.WRITE)
	if endpoint == null:
		_server.stop()
		push_error("Godot MCP could not write endpoint discovery file.")
		return
	endpoint.store_string(JSON.stringify({
		"version": 1, "host": LISTEN_HOST, "port": _server.get_local_port(),
		"token": _token, "pid": OS.get_process_id(),
		"project_path": ProjectSettings.globalize_path("res://"),
	}))
	endpoint.close()
	FileAccess.set_unix_permissions(ENDPOINT_PATH, 0x180)
	_endpoint_owned = true
	set_process(true)
	print("GODOT_MCP: listening on %s:%d" % [LISTEN_HOST, _server.get_local_port()])


func _exit_tree() -> void:
	set_process(false)
	for client in _clients:
		client["peer"].disconnect_from_host()
	_clients.clear()
	_requests.clear()
	_pending_runtime.clear()
	if _endpoint_owned:
		DirAccess.remove_absolute(ProjectSettings.globalize_path(ENDPOINT_PATH))
	if _server.is_listening():
		_server.stop()
	if _runtime_debugger != null:
		remove_debugger_plugin(_runtime_debugger)
		_runtime_debugger = null


func _disable_plugin() -> void:
	var current := str(ProjectSettings.get_setting("autoload/%s" % RUNTIME_AUTOLOAD_NAME, ""))
	if current.trim_prefix("*") == RUNTIME_AUTOLOAD_PATH:
		remove_autoload_singleton(RUNTIME_AUTOLOAD_NAME)


func _process(_delta: float) -> void:
	# Save/import progress can run a nested editor loop. Never dispatch the same packet twice.
	if _polling:
		return
	_polling = true
	_accept_connection()
	_poll_connection()
	_expire_runtime_requests()
	_polling = false


func _ensure_runtime_autoload() -> void:
	var setting := "autoload/%s" % RUNTIME_AUTOLOAD_NAME
	var current := str(ProjectSettings.get_setting(setting, ""))
	if current.is_empty():
		add_autoload_singleton(RUNTIME_AUTOLOAD_NAME, RUNTIME_AUTOLOAD_PATH)
	elif current.trim_prefix("*") != RUNTIME_AUTOLOAD_PATH:
		push_error("Godot MCP autoload name is already used: %s" % RUNTIME_AUTOLOAD_NAME)


func _accept_connection() -> void:
	while _server.is_connection_available():
		var peer := _server.take_connection()
		if _clients.size() >= 16:
			peer.disconnect_from_host()
			continue
		peer.set_no_delay(true)
		_clients.append({"peer": peer, "buffer": PackedByteArray()})


func _poll_connection() -> void:
	for index in range(_clients.size() - 1, -1, -1):
		var client := _clients[index]
		var peer: StreamPeerTCP = client["peer"]
		peer.poll()
		if peer.get_status() != StreamPeerSocket.STATUS_CONNECTED:
			_clients.remove_at(index)
			continue
		var available := mini(peer.get_available_bytes(), 65536)
		if available > 0:
			var packet := peer.get_data(available)
			if int(packet[0]) != OK:
				peer.disconnect_from_host()
				continue
			client["buffer"].append_array(packet[1])
		var buffer: PackedByteArray = client["buffer"]
		if buffer.size() > 1048576:
			peer.disconnect_from_host()
			continue
		for _line_index in range(32):
			var newline := buffer.find(10)
			if newline < 0:
				break
			var line := buffer.slice(0, newline).get_string_from_utf8().strip_edges()
			buffer = buffer.slice(newline + 1)
			client["buffer"] = buffer
			if not line.is_empty():
				_handle_line(line, peer)
		client["buffer"] = buffer


func _handle_line(line: String, peer: StreamPeerTCP) -> void:
	var request = JSON.parse_string(line)
	var request_id := _next_rpc_id
	_next_rpc_id += 1
	_requests[request_id] = {
		"peer": peer, "id": request.get("id") if request is Dictionary else null,
		"deadline": Time.get_ticks_msec() + REQUEST_TIMEOUT_MSEC,
	}
	if not request is Dictionary:
		_send_error(request_id, "INVALID_REQUEST", "Expected one JSON object per line.")
		return
	if request.get("token") != _token:
		_send_error(request_id, "UNAUTHORIZED", "Use this project's endpoint discovery file.")
		return
	var method := str(request.get("method", ""))
	if not request.get("params", {}) is Dictionary:
		_send_error(request_id, "INVALID_REQUEST", "params must be an object.")
		return
	var params: Dictionary = request.get("params", {})
	if not params.get("arguments", {}) is Dictionary:
		_send_error(request_id, "INVALID_ARGUMENT", "arguments must be an object.")
		return
	if method != "invoke":
		_send_error(request_id, "METHOD_NOT_FOUND", "Unknown bridge method: %s" % method)
		return
	_invoke_tool(request_id, str(params.get("tool", "")), params.get("arguments", {}))


func _invoke_tool(request_id: Variant, tool: String, arguments: Dictionary) -> void:
	match tool:
		"godot_get_state":
			_send_ok(request_id, _get_editor_state())
		"godot_get_open_scenes":
			_send_ok(request_id, _get_open_scenes())
		"godot_close_scene":
			_close_scene(request_id, arguments)
		"godot_open_scene":
			_open_scene(request_id, arguments)
		"godot_list_commands":
			_send_ok(request_id, _list_allowed_commands())
		"godot_run_command":
			_run_command(request_id, arguments)
		"godot_play":
			_play(request_id, arguments)
		"godot_stop":
			EditorInterface.stop_playing_scene()
			_send_ok(request_id, _get_editor_state())
		"godot_capture":
			_capture(request_id)
		"godot_get_edited_scene":
			_send_ok(request_id, _get_edited_scene(arguments))
		"godot_inspect_editor_node":
			_inspect_editor_node(request_id, arguments)
		"godot_set_editor_property":
			_set_editor_property(request_id, arguments)
		"godot_create_editor_node":
			_create_editor_node(request_id, arguments)
		"godot_delete_editor_node":
			_delete_editor_node(request_id, arguments)
		"godot_editor_undo", "godot_editor_redo":
			_editor_history(request_id, tool == "godot_editor_redo")
		"godot_reload_project":
			_reload_project(request_id)
		"godot_get_logs":
			_get_logs(request_id, arguments)
		"godot_save_scene":
			var error := EditorInterface.save_scene()
			if error == OK:
				_send_ok(request_id, {"saved": true})
			else:
				_send_error(request_id, "SAVE_FAILED", error_string(error))
		"godot_get_runtime_tree":
			_send_runtime(request_id, "get_runtime_tree", arguments)
		"godot_inspect_runtime_object":
			_send_runtime(request_id, "inspect_runtime_object", arguments)
		"godot_set_runtime_property":
			_send_runtime(request_id, "set_runtime_property", arguments)
		"godot_send_input_action":
			_send_runtime(request_id, "send_input_action", arguments)
		"godot_send_mouse_event":
			_send_runtime(request_id, "send_mouse_event", arguments)
		"godot_wait_runtime_frames":
			_send_runtime(request_id, "wait_runtime_frames", arguments)
		_:
			_send_error(request_id, "TOOL_NOT_FOUND", "Unknown Godot MCP tool: %s" % tool)


func _get_editor_state() -> Dictionary:
	var scene_root := EditorInterface.get_edited_scene_root()
	return {
		"godot_version": Engine.get_version_info().get("string", "unknown"),
		"project_path": ProjectSettings.globalize_path("res://"),
		"edited_scene": scene_root.scene_file_path if scene_root != null else "",
		"playing": EditorInterface.is_playing_scene(),
		"playing_scene": EditorInterface.get_playing_scene(),
		"runtime_session_active": _runtime_debugger != null and _runtime_debugger.has_active_session(),
		"open_scenes": Array(EditorInterface.get_open_scenes()),
		"unsaved_scenes": Array(EditorInterface.get_unsaved_scenes()),
		"filesystem_scanning": EditorInterface.get_resource_filesystem().is_scanning(),
		"mcp_version": "0.2.0",
		"native_log_available": EditorInterface.has_method("get_editor_log"),
	}


func _get_open_scenes() -> Dictionary:
	var root := EditorInterface.get_edited_scene_root()
	return {
		"open_scenes": Array(EditorInterface.get_open_scenes()),
		"unsaved_scenes": Array(EditorInterface.get_unsaved_scenes()),
		"edited_scene": root.scene_file_path if root != null else "",
	}


func _close_scene(request_id: Variant, arguments: Dictionary) -> void:
	var scene_path := ProjectSettings.localize_path(str(arguments.get("scene_path", "")))
	if scene_path.is_empty():
		_send_error(request_id, "INVALID_ARGUMENT", "scene_path is required.")
		return

	var open_scenes := EditorInterface.get_open_scenes()
	if not open_scenes.has(scene_path):
		_send_error(request_id, "SCENE_NOT_OPEN", "Scene is not open in the editor: %s" % scene_path)
		return
	if EditorInterface.get_unsaved_scenes().has(scene_path) and not bool(arguments.get("force", false)):
		_send_error(request_id, "UNSAVED_CHANGES", "Scene has unsaved editor changes; pass force=true to discard them: %s" % scene_path)
		return

	var error := EditorInterface.close_scene_from_path(scene_path)
	if error != OK:
		_send_error(request_id, "CLOSE_FAILED", error_string(error))
		return
	_send_ok(request_id, {"closed": true, "scene_path": scene_path})


func _open_scene(request_id: Variant, arguments: Dictionary) -> void:
	var scene_path := ProjectSettings.localize_path(str(arguments.get("scene_path", "")))
	if scene_path.is_empty():
		_send_error(request_id, "INVALID_ARGUMENT", "scene_path is required.")
		return
	if not FileAccess.file_exists(scene_path) or not ["tscn", "scn"].has(scene_path.get_extension().to_lower()):
		_send_error(request_id, "SCENE_NOT_FOUND", "Scene file does not exist: %s" % scene_path)
		return

	EditorInterface.open_scene_from_path(scene_path)
	var root := EditorInterface.get_edited_scene_root()
	if root == null or root.scene_file_path != scene_path:
		_send_error(request_id, "OPEN_FAILED", "Editor did not open the requested scene: %s" % scene_path)
		return
	_send_ok(request_id, {"opened": true, "scene_path": scene_path})


func _list_allowed_commands() -> Dictionary:
	var registered := EditorInterface.get_command_palette().get_command_keys()
	var commands: Array = []
	for key in COMMAND_ALLOWLIST:
		if registered.has(key):
			commands.append(key)
	commands.sort()
	return {"commands": commands}


func _run_command(request_id: Variant, arguments: Dictionary) -> void:
	var key := str(arguments.get("key", ""))
	if not COMMAND_ALLOWLIST.has(key):
		_send_error(request_id, "COMMAND_NOT_ALLOWED", "Command is not in the fixed allowlist: %s" % key)
		return
	var palette := EditorInterface.get_command_palette()
	if not palette.get_command_keys().has(key):
		_send_error(request_id, "COMMAND_NOT_FOUND", "Command is not registered: %s" % key)
		return
	palette.execute_command(key)
	_send_ok(request_id, {"executed": key})


func _play(request_id: Variant, arguments: Dictionary) -> void:
	var mode := str(arguments.get("mode", "main"))
	match mode:
		"main":
			EditorInterface.play_main_scene()
		"current":
			EditorInterface.play_current_scene()
		"custom":
			var scene_path := str(arguments.get("scene_path", ""))
			if scene_path.is_empty():
				_send_error(request_id, "INVALID_ARGUMENT", "scene_path is required for custom mode.")
				return
			EditorInterface.play_custom_scene(scene_path)
		_:
			_send_error(request_id, "INVALID_ARGUMENT", "Unknown play mode: %s" % mode)
			return
	_send_ok(request_id, {"requested": true, "mode": mode})


func _capture(request_id: Variant) -> void:
	var accepted := EditorInterface.request_game_screenshot(_on_screenshot_ready.bind(request_id))
	if not accepted:
		_send_error(request_id, "CAPTURE_UNAVAILABLE", "No running game is ready for capture.")


func _on_screenshot_ready(width: int, height: int, path: String, request_id: Variant) -> void:
	if not _requests.has(request_id) or _requests[request_id]["peer"].get_status() != StreamPeerSocket.STATUS_CONNECTED:
		DirAccess.remove_absolute(path)
		return
	_send_ok(request_id, {"width": width, "height": height, "path": path})


func _get_edited_scene(arguments: Dictionary) -> Dictionary:
	var root := EditorInterface.get_edited_scene_root()
	if root == null:
		return {"nodes": [], "scene_path": ""}
	var nodes: Array = []
	_append_editor_tree(root, 0, clampi(int(arguments.get("max_depth", 16)), 0, 64), nodes)
	return {"scene_path": root.scene_file_path, "nodes": nodes}


func _append_editor_tree(node: Node, depth: int, max_depth: int, output: Array) -> void:
	output.append({"path": str(node.get_path()), "name": str(node.name), "type": node.get_class(), "depth": depth, "child_count": node.get_child_count()})
	if depth >= max_depth:
		return
	for child in node.get_children():
		_append_editor_tree(child, depth + 1, max_depth, output)


func _inspect_editor_node(request_id: Variant, arguments: Dictionary) -> void:
	var node: Node = _find_editor_node(str(arguments.get("node_path", "")))
	if node == null:
		_send_error(request_id, "OBJECT_NOT_FOUND", "Edited scene node was not found.")
		return
	var requested: Array = arguments.get("properties", [])
	var properties: Array = []
	for info in node.get_property_list():
		var property_name := str(info.get("name", ""))
		var usage := int(info.get("usage", 0))
		if property_name.is_empty() or usage & (PROPERTY_USAGE_CATEGORY | PROPERTY_USAGE_GROUP | PROPERTY_USAGE_SUBGROUP):
			continue
		if not requested.is_empty() and not requested.has(property_name):
			continue
		properties.append({"name": property_name, "type": int(info.get("type", TYPE_NIL)), "usage": usage, "value": _to_wire_value(node.get(property_name))})
	_send_ok(request_id, {"path": str(node.get_path()), "class": node.get_class(), "properties": properties})


func _set_editor_property(request_id: Variant, arguments: Dictionary) -> void:
	var node: Node = _find_editor_node(str(arguments.get("node_path", "")))
	if node == null:
		_send_error(request_id, "OBJECT_NOT_FOUND", "Edited scene node was not found.")
		return
	var property_name := str(arguments.get("property", ""))
	if property_name.is_empty() or not _has_property(node, property_name):
		_send_error(request_id, "PROPERTY_NOT_FOUND", "Edited scene property was not found: %s" % property_name)
		return
	var old_value = node.get(property_name)
	var new_value = _from_wire_value(arguments.get("value"))
	var undo := get_undo_redo()
	undo.create_action("MCP: Set %s" % property_name)
	undo.add_do_property(node, property_name, new_value)
	undo.add_undo_property(node, property_name, old_value)
	undo.commit_action()
	_send_ok(request_id, {"property": property_name, "value": _to_wire_value(node.get(property_name))})


func _find_editor_node(path: String):
	var root := EditorInterface.get_edited_scene_root()
	if root == null:
		return null
	if path.is_empty() or path == "." or path == str(root.get_path()):
		return root
	var node := root.get_node_or_null(NodePath(path))
	if node != null and (node == root or root.is_ancestor_of(node)):
		return node
	if path.begins_with(str(root.get_path()) + "/"):
		return root.get_node_or_null(NodePath(path.trim_prefix(str(root.get_path()) + "/")))
	return null


func _create_editor_node(request_id: Variant, arguments: Dictionary) -> void:
	var root := EditorInterface.get_edited_scene_root()
	var parent: Node = _find_editor_node(str(arguments.get("parent_path", ".")))
	if root == null or parent == null:
		_send_error(request_id, "OBJECT_NOT_FOUND", "Edited scene parent was not found.")
		return
	if parent != root and parent.owner != root:
		_send_error(request_id, "NOT_EDITABLE", "Parent belongs to an instantiated or inherited scene.")
		return
	var node_name := str(arguments.get("name", ""))
	if node_name.is_empty() or node_name != node_name.validate_node_name() or parent.has_node(NodePath(node_name)):
		_send_error(request_id, "INVALID_ARGUMENT", "name must be a unique, valid child node name.")
		return
	var node_type := str(arguments.get("type", "Node"))
	var script_path := str(arguments.get("script_path", ""))
	var script: Script
	if not script_path.is_empty():
		if not script_path.begins_with("res://") or not ResourceLoader.exists(script_path, "Script"):
			_send_error(request_id, "INVALID_ARGUMENT", "script_path must identify a project script.")
			return
		script = load(script_path) as Script
		if script == null or not script.can_instantiate():
			_send_error(request_id, "INVALID_SCRIPT", "Script cannot be instantiated.")
			return
		if not arguments.has("type"):
			node_type = script.get_instance_base_type()
		if not ClassDB.is_parent_class(node_type, script.get_instance_base_type()):
			_send_error(request_id, "INVALID_ARGUMENT", "Node type does not inherit the script's base type.")
			return
	if not ClassDB.class_exists(node_type) or not ClassDB.is_parent_class(node_type, "Node") or not ClassDB.can_instantiate(node_type):
		_send_error(request_id, "INVALID_ARGUMENT", "type must be an instantiable Node class.")
		return
	var node: Node = ClassDB.instantiate(node_type)
	node.name = node_name
	if script != null:
		node.set_script(script)
	var properties: Dictionary = arguments.get("properties", {})
	for key in properties:
		if key in ["name", "owner", "script"] or not _has_property(node, str(key)):
			node.free()
			_send_error(request_id, "PROPERTY_NOT_FOUND", "Invalid initial property: %s" % key)
			return
	for key in properties:
		node.set(str(key), _from_wire_value(properties[key]))
	var undo := get_undo_redo()
	undo.create_action("MCP: Create %s" % node_name, UndoRedo.MERGE_DISABLE, root)
	undo.add_do_method(parent, "add_child", node, true)
	undo.add_do_property(node, "owner", root)
	undo.add_do_reference(node)
	undo.add_undo_method(parent, "remove_child", node)
	undo.commit_action()
	_send_ok(request_id, {"created": true, "node_path": str(root.get_path_to(node)), "class": node.get_class()})


func _delete_editor_node(request_id: Variant, arguments: Dictionary) -> void:
	var root := EditorInterface.get_edited_scene_root()
	var node: Node = _find_editor_node(str(arguments.get("node_path", "")))
	if node == null:
		_send_error(request_id, "OBJECT_NOT_FOUND", "Edited scene node was not found.")
		return
	if node == root or node.owner != root:
		_send_error(request_id, "NOT_EDITABLE", "Cannot delete the scene root or an inherited/instantiated child.")
		return
	var path := str(root.get_path_to(node))
	var parent := node.get_parent()
	var owners: Array = []
	_collect_owners(node, owners)
	var undo := get_undo_redo()
	undo.create_action("MCP: Delete %s" % node.name, UndoRedo.MERGE_DISABLE, root)
	undo.add_do_method(EditorInterface.get_selection(), "clear")
	undo.add_do_method(parent, "remove_child", node)
	undo.add_undo_method(parent, "add_child", node)
	undo.add_undo_method(parent, "move_child", node, node.get_index())
	for entry in owners:
		undo.add_undo_property(entry[0], "owner", entry[1])
	undo.add_undo_reference(node)
	undo.commit_action()
	_send_ok(request_id, {"deleted": true, "node_path": path})


func _collect_owners(node: Node, output: Array) -> void:
	output.append([node, node.owner])
	for child in node.get_children():
		_collect_owners(child, output)


func _editor_history(request_id: Variant, redo: bool) -> void:
	var root := EditorInterface.get_edited_scene_root()
	if root == null:
		_send_error(request_id, "OBJECT_NOT_FOUND", "No edited scene.")
		return
	var manager := get_undo_redo()
	var history := manager.get_history_undo_redo(manager.get_object_history_id(root))
	if (redo and not history.has_redo()) or (not redo and not history.has_undo()):
		_send_error(request_id, "EMPTY_HISTORY", "No scene action available.")
		return
	if redo:
		history.redo()
	else:
		history.undo()
	_send_ok(request_id, _get_open_scenes())


func _reload_project(request_id: Variant) -> void:
	var filesystem := EditorInterface.get_resource_filesystem()
	if filesystem.is_scanning():
		_send_error(request_id, "BUSY", "Filesystem scan already in progress.")
		return
	filesystem.filesystem_changed.connect(_on_project_scanned.bind(request_id), CONNECT_ONE_SHOT)
	filesystem.scan()


func _on_project_scanned(request_id: Variant) -> void:
	if not _requests.has(request_id):
		return
	EditorInterface.get_script_editor().reload_open_files()
	_send_ok(request_id, {"reloaded": true, "state": _get_editor_state()})


func _get_logs(request_id: Variant, arguments: Dictionary) -> void:
	if not EditorInterface.has_method("get_editor_log"):
		_send_error(request_id, "CAPABILITY_UNAVAILABLE", "Rebuild the custom editor for get_editor_log.")
		return
	var cursor := int(arguments.get("cursor", 0))
	var latest: Dictionary = EditorInterface.call("get_editor_log", 0, 1)
	if cursor < 0 or cursor > int(latest.get("latest_cursor", 0)):
		_send_error(request_id, "INVALID_CURSOR", "Cursor is outside this editor process; restart at 0.")
		return
	var result: Dictionary = EditorInterface.call("get_editor_log", cursor, clampi(int(arguments.get("max_entries", 200)), 1, 2048))
	var levels: Array = arguments.get("levels", [])
	var entries: Array = []
	var names := ["info", "error", "info", "warning", "editor"]
	for entry in result["entries"]:
		entry["level"] = names[int(entry["type"])]
		if levels.is_empty() or levels.has(entry["level"]):
			entries.append(entry)
	result["entries"] = entries
	result["editor_pid"] = OS.get_process_id()
	_send_ok(request_id, result)


func _has_property(object: Object, property_name: String) -> bool:
	for info in object.get_property_list():
		if str(info.get("name", "")) == property_name:
			return true
	return false


func _send_runtime(rpc_id: Variant, operation: String, arguments: Dictionary) -> void:
	if _runtime_debugger == null:
		_send_error(rpc_id, "NO_ACTIVE_SESSION", "Runtime debugger is unavailable.")
		return
	var runtime_id: int = _runtime_debugger.send_runtime_request(operation, arguments)
	if runtime_id < 0:
		_send_error(rpc_id, "NO_ACTIVE_SESSION", "No running debug game is connected.")
		return
	_pending_runtime[runtime_id] = {"rpc_id": rpc_id, "deadline": Time.get_ticks_msec() + REQUEST_TIMEOUT_MSEC}


func _on_runtime_response(runtime_id: int, ok: bool, payload: Variant) -> void:
	if not _pending_runtime.has(runtime_id):
		return
	var rpc_id = _pending_runtime[runtime_id]["rpc_id"]
	_pending_runtime.erase(runtime_id)
	if ok:
		_send_ok(rpc_id, payload)
	else:
		var error: Dictionary = payload if payload is Dictionary else {"code": "RUNTIME_ERROR", "message": str(payload)}
		_send_error(rpc_id, str(error.get("code", "RUNTIME_ERROR")), str(error.get("message", "Runtime request failed.")))


func _on_runtime_state_changed() -> void:
	if _runtime_debugger.has_active_session():
		return
	for pending in _pending_runtime.values():
		_send_error(pending["rpc_id"], "NO_ACTIVE_SESSION", "The debug game disconnected.")
	_pending_runtime.clear()


func _expire_runtime_requests() -> void:
	var now := Time.get_ticks_msec()
	var expired: Array = []
	for runtime_id in _pending_runtime:
		if now >= int(_pending_runtime[runtime_id]["deadline"]):
			expired.append(runtime_id)
	for runtime_id in expired:
		var rpc_id = _pending_runtime[runtime_id]["rpc_id"]
		_pending_runtime.erase(runtime_id)
		_send_error(rpc_id, "TIMEOUT", "Runtime request timed out.")
	for rpc_id in _requests.keys():
		if now >= int(_requests[rpc_id]["deadline"]):
			_send_error(rpc_id, "TIMEOUT", "Editor request timed out.")


func _send_ok(request_id: Variant, result: Variant) -> void:
	_send_message({"id": request_id, "ok": true, "result": result})


func _send_error(request_id: Variant, code: String, message: String) -> void:
	_send_message({"id": request_id, "ok": false, "error": {"code": code, "message": message}})


func _send_message(message: Dictionary) -> void:
	var request_id = message.get("id")
	if not _requests.has(request_id):
		return
	var request: Dictionary = _requests[request_id]
	_requests.erase(request_id)
	var peer: StreamPeerTCP = request["peer"]
	if peer.get_status() == StreamPeerSocket.STATUS_CONNECTED:
		message["id"] = request["id"]
		peer.put_data((JSON.stringify(message) + "\n").to_utf8_buffer())


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
			var output: Array = []
			for item in value:
				output.append(_to_wire_value(item, depth + 1))
			return output
		TYPE_DICTIONARY:
			var output := {}
			for key in value:
				output[str(key)] = _to_wire_value(value[key], depth + 1)
			return output
		TYPE_OBJECT:
			if value == null:
				return null
			var object: Object = value
			return {"_type": object.get_class(), "instance_id": object.get_instance_id()}
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
