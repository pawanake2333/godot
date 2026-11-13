@tool
extends EditorPlugin

const EditorTools = preload("res://addons/godot_mcp/tools/editor_tools.gd")
var _editor_tools: RefCounted

const RuntimeDebugger = preload("res://addons/godot_mcp/editor/runtime_debugger.gd")
const RUNTIME_AUTOLOAD_NAME := "GodotMCPRuntime"
const RUNTIME_AUTOLOAD_PATH := "res://addons/godot_mcp/runtime/runtime_helper.gd"
const LISTEN_HOST := "127.0.0.1"
const ENDPOINT_PATH := "res://.godot/mcp/endpoint.json"
const REQUEST_TIMEOUT_MSEC := 10000
var _server := TCPServer.new()
var _clients: Array[Dictionary] = []
var _requests := {}
var _next_rpc_id := 1
var _token := ""
var _editor_epoch := Crypto.new().generate_random_bytes(16).hex_encode()
var _endpoint_owned := false
var _polling := false
var _runtime_debugger: EditorDebuggerPlugin
var _pending_runtime := {}
var _package := {}
var history_revision := 1

func _on_history_changed() -> void:
	history_revision += 1


func _enter_tree() -> void:
	for argument in OS.get_cmdline_args():
		if argument in ["--export-pack", "--export-debug", "--export-release", "--export-patch", "--import"]:
			set_process(false)
			return
	get_undo_redo().history_changed.connect(_on_history_changed)
	get_undo_redo().version_changed.connect(_on_history_changed)
	_package = _package_identity()
	if FileAccess.file_exists(ENDPOINT_PATH):
		var existing = JSON.parse_string(FileAccess.get_file_as_string(ENDPOINT_PATH))
		if existing is Dictionary and int(existing.get("port", 0)) > 0:
			# Unix process-status APIs may only accept child PIDs. Probe the endpoint
			# listener instead, so a stale file after a crash causes no engine error.
			var probe := TCPServer.new()
			if probe.listen(int(existing.port), LISTEN_HOST) != OK:
				push_error("Godot MCP endpoint is still listening for this project.")
				set_process(false)
				return
			probe.stop()
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
			if _runtime_debugger != null:
				for session in _runtime_debugger.list_sessions():
					_runtime_debugger.send_runtime_request("godot_input_release_all", {"session_id": session.session_id, "_input_owner": str(peer.get_instance_id())})
			for rpc_id in _requests.keys():
				if _requests[rpc_id].peer == peer:
					_requests.erase(rpc_id)
			for runtime_id in _pending_runtime.keys():
				if not _requests.has(_pending_runtime[runtime_id].rpc_id):
					_runtime_debugger.forget_request(int(runtime_id))
					_pending_runtime.erase(runtime_id)
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
		if buffer.size() > 4194304:
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
	if _requests.size() > 128:
		_send_error(request_id, "BUSY", "Editor request budget exhausted; request was not submitted")
		return
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
	_requests[request_id]["tool"] = str(params.get("tool", ""))
	_requests[request_id]["target"] = {}
	for key in ["scene_id", "session_id", "document_id", "expected_revision"]:
		if params.get("arguments", {}).has(key):
			_requests[request_id].target[key] = params.arguments[key]
	_invoke_tool(request_id, str(params.get("tool", "")), params.get("arguments", {}))


func _invoke_tool(request_id: Variant, tool: String, arguments: Dictionary) -> void:
	if tool == "godot_output_control":
		_output_control(request_id, arguments)
		return
	if tool == "_editor_ready":
		var deadline := Time.get_ticks_msec() + 9000
		while not EditorInterface.is_editor_ready() or EditorInterface.get_resource_filesystem().is_scanning():
			if Time.get_ticks_msec() >= deadline:
				_send_error(request_id, "EDITOR_NOT_READY", "Initial filesystem scan/layout restoration did not finish")
				return
			await get_tree().process_frame
		_send_ok(request_id, _get_editor_state())
		return
	if tool == "godot_editor_commit_close":
		if str(arguments.get("editor_epoch", "")) != _editor_epoch:
			_send_error(request_id, "EDITOR_CONFLICT", "Editor epoch changed")
			return
		if str(arguments.get("expected_document_revision", "")) != _document_revision():
			_send_error(request_id, "DOCUMENT_CONFLICT", "Open documents changed since the supplied editor status")
			return
		var other_documents := EditorInterface.get_unsaved_editor_state()
		var policy := str(arguments.get("unsaved", "reject"))
		# Shader/custom resource editors own buffers not covered by scene/script saves.
		# Never close them implicitly, even under the partial save policy.
		if not other_documents.plugins.is_empty():
			_send_error(request_id, "UNTRACKED_DIRTY_DOCUMENT", "Other editor plugins have unsaved buffers without a revision contract; save them in their native editor before closing")
			return
		if policy != "discard" and other_documents.global_history_unsaved:
			_send_error(request_id, "UNSAVED_EDITOR_RESOURCES", "Save other resource/plugin documents in the editor, or explicitly discard them")
			return
		var dirty_scenes := EditorInterface.get_unsaved_scenes()
		var dirty_scripts := EditorInterface.get_script_editor().get_unsaved_files()
		if (not dirty_scenes.is_empty() or not dirty_scripts.is_empty()) and policy == "reject":
			_send_error(request_id, "UNSAVED_CHANGES", "Save scene/script documents or explicitly discard them")
			return
		if policy == "save":
			if "" in dirty_scenes:
				_send_error(request_id, "PATH_REQUIRED", "Save untitled scenes to explicit paths before closing")
				return
			EditorInterface.get_script_editor().save_all_scripts()
			EditorInterface.save_all_scenes()
			if not EditorInterface.get_unsaved_scenes().is_empty() or not EditorInterface.get_script_editor().get_unsaved_files().is_empty():
				_send_error(request_id, "SAVE_FAILED", "Unsaved documents remain")
				return
		var root := EditorInterface.get_edited_scene_root()
		var scripts: Array[String] = []
		for script in EditorInterface.get_script_editor().get_open_scripts():
			if script.resource_path.begins_with("res://") and not script.resource_path.contains("::"):
				scripts.append(script.resource_path)
		var active := EditorInterface.get_script_editor().get_current_script()
		_send_ok(request_id, {"documents": EditorInterface.get_open_scenes(), "active_scene": root.scene_file_path if root != null else "", "script_documents": scripts, "active_script": active.resource_path if active != null else ""})
		EditorInterface.call_deferred("quit_editor")
		return
	if tool == "_restore_script_documents":
		var restored: Array[String] = []
		for path in arguments.get("paths", []):
			if str(path).begins_with("res://") and ResourceLoader.exists(path, "Script"):
				var script := load(path) as Script
				if script != null:
					EditorInterface.edit_script(script, -1, 0, false)
					restored.append(path)
		var active := str(arguments.get("active", ""))
		if active in restored:
			EditorInterface.edit_script(load(active), -1, 0, false)
		_send_ok(request_id, {"restored": restored})
		return
	if tool == "godot_profiler_capture":
		var result: Dictionary = await _runtime_debugger.profiler_capture(arguments)
		if result.ok:
			_send_ok(request_id, result.result)
		else:
			_send_error(request_id, result.error.code, result.error.message)
		return
	if tool in ["godot_debugger_breakpoint_set", "godot_debugger_breakpoint_clear", "godot_debugger_state", "godot_debugger_control"]:
		var result: Dictionary = await _runtime_debugger.debug_invoke(tool, arguments)
		if result.ok:
			_send_ok(request_id, result.result)
		else:
			_send_error(request_id, result.error.code, result.error.message)
		return
	if tool == "godot_debugger_prepare":
		var status: int = EditorInterface.call("start_debugger_server")
		if status != OK:
			_send_error(request_id, "DEBUGGER_UNAVAILABLE", error_string(status))
		else:
			_send_ok(request_id, {"debugger_uri": EditorInterface.call("get_debugger_server_uri")})
		return
	if tool == "godot_run_list":
		_send_ok(request_id, {"sessions": _runtime_debugger.list_sessions(), "debugger_uri": EditorInterface.call("get_debugger_server_uri") if EditorInterface.has_method("get_debugger_server_uri") else ""})
		return
	if tool == "godot_run_wait":
		var response: Dictionary
		if arguments.has("pid") or arguments.has("session_id"):
			response = await _runtime_debugger.wait_stage(arguments)
		else:
			response = await _runtime_debugger.wait_sessions(int(arguments.get("minimum_sessions", 1)), int(arguments.get("timeout_ms", 5000)))
		if response.ok:
			_send_ok(request_id, response.result)
		else:
			_send_error(request_id, response.error.code, response.error.message)
		return
	var unified_tools := ["godot_animation_list", "godot_animation_inspect", "godot_animation_sample", "godot_object_call", "godot_object_inspect", "godot_object_set_properties", "godot_object_release", "godot_scene_tree", "godot_object_query"]
	if tool in ["godot_capture_frame", "godot_monitors_read", "godot_runtime_stop", "godot_ui_focus", "godot_ui_inspect", "godot_input_sequence", "godot_input_send", "godot_input_release_all", "godot_runtime_wait"] or (tool in unified_tools and arguments.get("context", "editor") == "runtime"):
		if not arguments.has("session_id"):
			_send_error(request_id, "SESSION_REQUIRED", "Select a session from godot_run_list")
			return
		arguments["_input_owner"] = str(_requests[request_id].peer.get_instance_id())
		_send_runtime(request_id, tool, arguments)
		return
	if (tool == "godot_object_set_properties" or (tool == "godot_object_call" and arguments.get("allow_mutation", false))) and not arguments.has("expected_revision"):
		_send_error(request_id, "REVISION_REQUIRED", "Editor mutation requires expected_revision")
		return
	if tool in unified_tools and tool != "godot_object_release" and not arguments.has("scene_id"):
		_send_error(request_id, "SCENE_REQUIRED", "Select an explicit editor document")
		return
	if tool in ["godot_edit_batch", "godot_animation_edit"] and _runtime_debugger != null and _runtime_debugger.has_active_session():
		_send_error(request_id, "LIVE_EDIT_UNSUPPORTED", "Stop running sessions before editor structure/animation edits; use explicit runtime tools for temporary game mutations")
		return
	if tool in ["godot_script_read", "godot_script_patch", "godot_project_settings_set", "godot_editor_select", "godot_animation_list", "godot_animation_inspect", "godot_animation_sample", "godot_animation_edit", "godot_object_call", "godot_script_reload", "godot_resource_reimport", "godot_scene_tree", "godot_object_query", "godot_filesystem_scan", "godot_edit_batch", "godot_scene_create", "godot_scene_open", "godot_scene_save", "godot_scene_close", "godot_scene_undo", "godot_scene_redo", "godot_scene_list", "godot_class_describe", "godot_resource_load", "godot_resource_create", "godot_resource_duplicate", "godot_resource_save", "godot_object_release", "godot_object_inspect", "godot_object_set_properties", "godot_project_settings_get"]:
		if _editor_tools == null:
			_editor_tools = EditorTools.new(self)
		var response: Dictionary = await _editor_tools.invoke(tool, arguments)
		if response.ok:
			_send_ok(request_id, response.result)
		else:
			_send_error(request_id, response.error.code, response.error.message)
		return
	if tool == "_editor_state":
		_send_ok(request_id, _get_editor_state())
	elif tool == "_editor_logs":
		_get_logs(request_id, arguments)
	else:
		_send_error(request_id, "TOOL_NOT_FOUND", "Unknown editor operation: " + tool)


func _document_revision() -> String:
	var hash := HashingContext.new()
	hash.start(HashingContext.HASH_SHA256)
	hash.update(str(history_revision).to_utf8_buffer())
	hash.update(JSON.stringify(EditorInterface.get_unsaved_editor_state()).to_utf8_buffer())
	var manager := get_undo_redo()
	for root in EditorInterface.get_open_scene_roots():
		var history := manager.get_history_undo_redo(manager.get_object_history_id(root))
		var path := root.scene_file_path
		var info := [str(root.get_instance_id()), path, history.get_version() if history != null else 0, path in EditorInterface.get_unsaved_scenes(), FileAccess.get_modified_time(path) if FileAccess.file_exists(path) else 0, FileAccess.get_size(path) if FileAccess.file_exists(path) else 0]
		hash.update(JSON.stringify(info).to_utf8_buffer())
	for document in EditorInterface.get_script_editor().get_open_script_editors():
		var resource: Resource = document.call("get_edited_resource")
		var editor := document.get_base_editor()
		if resource != null and editor is CodeEdit:
			var path := resource.resource_path
			hash.update(JSON.stringify([str(editor.get_instance_id()), path, editor.get_version(), editor.get_saved_version(), FileAccess.get_modified_time(path) if FileAccess.file_exists(path) else 0, FileAccess.get_size(path) if FileAccess.file_exists(path) else 0]).to_utf8_buffer())
	return hash.finish().hex_encode()

func _get_editor_state() -> Dictionary:
	var scene_root := EditorInterface.get_edited_scene_root()
	return {
		"editor_pid": OS.get_process_id(),
		"editor_epoch": _editor_epoch,
		"editor_ready": EditorInterface.is_editor_ready(),
		"document_revision": _document_revision(),
		"unsaved_scripts": EditorInterface.get_script_editor().get_unsaved_files(),
		"unsaved_editor_resources": EditorInterface.get_unsaved_editor_state(),
		"godot_version": Engine.get_version_info().get("string", "unknown"),
		"project_path": ProjectSettings.globalize_path("res://"),
		"edited_scene": scene_root.scene_file_path if scene_root != null else "",
		"playing": EditorInterface.is_playing_scene(),
		"playing_scene": EditorInterface.get_playing_scene(),
		"runtime_session_active": _runtime_debugger != null and _runtime_debugger.has_active_session(),
		"pending_runtime_requests": _pending_runtime.size(),
		"open_scenes": Array(EditorInterface.get_open_scenes()),
		"unsaved_scenes": Array(EditorInterface.get_unsaved_scenes()),
		"filesystem_scanning": EditorInterface.get_resource_filesystem().is_scanning(),
		"mcp_version": "0.6.1",
		"engine_build": Engine.get_version_info(),
		"package": _package,
		"native_api": {"editor_consistency": EditorInterface.has_method("edit_inspector_properties") and EditorInterface.has_method("add_undo_redo_property_change") and EditorInterface.has_method("add_scene_node_path_changes") and ClassDB.class_has_method("EditorUndoRedoManager", "undo_history") and ClassDB.class_has_method("ScriptEditor", "set_breakpoint"), "output_panel": EditorInterface.has_method("get_editor_output_state") and EditorInterface.has_method("clear_editor_output") and EditorInterface.has_method("configure_editor_output") and EditorInterface.has_method("read_editor_output"), "new_scene": EditorInterface.has_method("new_scene"), "get_editor_log": EditorInterface.has_method("get_editor_log"), "debugger_server": EditorInterface.has_method("start_debugger_server") and EditorInterface.has_method("get_debugger_server_uri"), "script_document_resource": ClassDB.class_has_method("ScriptEditorBase", "get_edited_resource"), "debug_control": ClassDB.class_has_method("EditorDebuggerSession", "debug_control"), "stack_variables": ClassDB.class_has_method("EditorDebuggerSession", "request_stack_variables"), "profiler_frames": ClassDB.class_has_signal("EditorDebuggerSession", "profiler_frame")},
		"native_log_available": EditorInterface.has_method("get_editor_log"),
	}


func _package_identity() -> Dictionary:
	var data = JSON.parse_string(FileAccess.get_file_as_string("res://addons/godot_mcp/package.json")) if FileAccess.file_exists("res://addons/godot_mcp/package.json") else null
	if not data is Dictionary:
		return {"verified": false, "reason": "package.json missing; generate it with scripts/deploy.py --manifest"}
	var mismatches: Array[String] = []
	for path in data.get("files", {}):
		if FileAccess.get_file_as_string("res://addons/godot_mcp/" + path).replace("\r\n", "\n").sha256_text() != data.files[path]:
			mismatches.append(path)
	return {"verified": mismatches.is_empty(), "version": data.get("version"), "protocol": data.get("protocol"), "source_sha256": data.get("source_sha256"), "mismatches": mismatches}


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
	var runtime_pid := 0
	if arguments.has("session_id"):
		for session in _runtime_debugger.list_sessions():
			if session.session_id == arguments.session_id:
				runtime_pid = int(session.pid)
		if runtime_pid == 0:
			_send_error(request_id, "SESSION_NOT_FOUND", "Diagnostic session filter requires a currently connected session")
			return
	var entries: Array = []
	var names := ["info", "error", "info", "warning", "editor"]
	for entry in result["entries"]:
		entry["level"] = names[int(entry["type"])]
		entry["source"] = entry.get("source", "editor")
		if arguments.has("source") and entry.source != arguments.source:
			continue
		if runtime_pid != 0 and int(entry.get("runtime_pid", 0)) != runtime_pid:
			continue
		if levels.is_empty() or levels.has(entry["level"]):
			entries.append(entry)
	result["entries"] = entries
	result["editor_pid"] = OS.get_process_id()
	result["attribution"] = "native debugger process/thread; editor messages are not attributed to requests"
	_send_ok(request_id, result)


func _output_control(request_id: Variant, arguments: Dictionary) -> void:
	for method in ["get_editor_output_state", "configure_editor_output", "clear_editor_output", "read_editor_output"]:
		if not EditorInterface.has_method(method):
			_send_error(request_id, "CAPABILITY_UNAVAILABLE", "Rebuild the custom editor for Output dock control")
			return
	var action := str(arguments.get("action", "get"))
	var state: Dictionary = EditorInterface.call("get_editor_output_state")
	var result: Dictionary
	match action:
		"get":
			result = state
		"configure":
			var options := {}
			for key in ["visible", "collapse", "search", "levels"]:
				if arguments.has(key):
					options[key] = arguments[key]
			var error: int = EditorInterface.call("configure_editor_output", options)
			if error != OK:
				_send_error(request_id, "INVALID_ARGUMENT", error_string(error))
				return
			result = EditorInterface.call("get_editor_output_state")
		"read":
			if arguments.has("editor_epoch") and str(arguments.editor_epoch) != _editor_epoch:
				_send_error(request_id, "EDITOR_CONFLICT", "Editor epoch changed; read a new first page")
				return
			if arguments.has("expected_revision") and int(arguments.expected_revision) != int(state.revision):
				_send_error(request_id, "OUTPUT_CHANGED", "Displayed Output changed; read a new first page")
				return
			result = EditorInterface.call("read_editor_output", int(arguments.get("offset", 0)), int(arguments.get("max_chars", 16384)))
		"clear":
			if str(arguments.get("editor_epoch", "")) != _editor_epoch:
				_send_error(request_id, "EDITOR_CONFLICT", "Editor epoch changed; query Output before clearing")
				return
			result = EditorInterface.call("clear_editor_output", str(arguments.get("scope", "panel")), int(arguments.get("through_cursor", -1)))
		_:
			_send_error(request_id, "INVALID_ARGUMENT", "Unknown Output action")
			return
	if result.has("error"):
		_send_error(request_id, str(result.error), str(result.message))
		return
	result["action"] = action
	result["editor_epoch"] = _editor_epoch
	_send_ok(request_id, result)


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
		_runtime_debugger.forget_request(int(runtime_id))
		_send_error(rpc_id, "TIMEOUT", "Runtime request timed out.")
	for rpc_id in _requests.keys():
		if now >= int(_requests[rpc_id]["deadline"]):
			_send_error(rpc_id, "TIMEOUT", "Editor request timed out.")


func _context(request_id: Variant, result: Variant = null) -> Dictionary:
	var request: Dictionary = _requests.get(request_id, {})
	var context := {"scope": "editor", "protocol": 2, "editor_epoch": _editor_epoch, "project_id": ProjectSettings.globalize_path("res://").trim_suffix("/").sha256_text(), "request_id": request_id, "tool": request.get("tool", ""), "target": request.get("target", {})}
	if result is Dictionary and result.has("revision"):
		context["revision_after"] = result.revision
	return context

func _send_ok(request_id: Variant, result: Variant) -> void:
	_send_message({"id": request_id, "ok": true, "result": result, "context": _context(request_id, result)})


func _send_error(request_id: Variant, code: String, message: String) -> void:
	_send_message({"id": request_id, "ok": false, "error": {"code": code, "message": message, "stage": "editor"}, "context": _context(request_id)})


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
