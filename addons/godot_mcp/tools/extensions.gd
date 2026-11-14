@tool
extends RefCounted

const MAX_CLASSES := 256
const MAX_MEMBERS := 16384

func fail(code: String, message: String, data: Dictionary = {}) -> Dictionary:
	return {"ok": false, "error": {"code": code, "message": message, "data": data}}

func snapshot(extension: GDExtension) -> Dictionary:
	var names: PackedStringArray = extension.call("get_registered_classes")
	if names.size() > MAX_CLASSES:
		return fail("BUDGET_EXCEEDED", "Extension class budget exceeded")
	var classes := {}
	var count := 0
	for name in names:
		if not ClassDB.class_exists(name):
			return fail("REGISTRATION_INVALID", "Registered extension class is absent from ClassDB: " + name)
		var item := {"parent": str(ClassDB.get_parent_class(name)), "methods": [], "properties": [], "signals": []}
		for pair in [["methods", ClassDB.class_get_method_list(name, true)], ["properties", ClassDB.class_get_property_list(name, true)], ["signals", ClassDB.class_get_signal_list(name, true)]]:
			for member in pair[1]:
				item[pair[0]].append(str(member.name))
				count += 1
				if count > MAX_MEMBERS:
					return fail("BUDGET_EXCEEDED", "Extension member budget exceeded")
			item[pair[0]].sort()
		classes[name] = item
	return {"ok": true, "classes": classes}

func begin(args: Dictionary, state: Dictionary) -> Dictionary:
	if str(args.editor_epoch) != str(state.editor_epoch):
		return fail("EDITOR_CONFLICT", "Editor epoch changed")
	if str(args.expected_document_revision) != str(state.document_revision):
		return fail("DOCUMENT_CONFLICT", "Open documents changed; read editor_status again")
	if not GDExtensionManager.has_method("get_reload_state") or not EditorInterface.has_method("is_extension_reload_complete"):
		return fail("NATIVE_API_UNAVAILABLE", "This editor lacks native reload completion tracking")
	if not state.editor_ready or state.filesystem_scanning:
		return fail("EDITOR_BUSY", "Wait for editor initialization and filesystem scanning")
	if state.playing or state.runtime_session_active:
		return fail("ACTIVE_GAME", "Stop running games before reloading a native extension")
	if not state.unsaved_scenes.is_empty() or not state.unsaved_scripts.is_empty() or state.unsaved_editor_resources.global_history_unsaved or not state.unsaved_editor_resources.plugins.is_empty():
		return fail("UNSAVED_CHANGES", "Save scene, script and resource documents before native reload", {"unsaved_scenes": state.unsaved_scenes, "unsaved_scripts": state.unsaved_scripts, "unsaved_editor_resources": state.unsaved_editor_resources})
	var path := str(args.path)
	if not path.begins_with("res://") or path.get_extension() != "gdextension" or path != ProjectSettings.localize_path(ProjectSettings.globalize_path(path).simplify_path()):
		return fail("INVALID_PATH", "Use a canonical res:// path to a project .gdextension file")
	if not FileAccess.file_exists(path):
		return fail("FILE_NOT_FOUND", path)
	if not GDExtensionManager.is_extension_loaded(path):
		return fail("EXTENSION_NOT_LOADED", "Load the project extension before requesting reload")
	var extension := GDExtensionManager.get_extension(path)
	for method in ["get_registered_classes", "get_library_path", "is_reloadable"]:
		if not extension.has_method(method):
			return fail("NATIVE_API_UNAVAILABLE", "This editor lacks native extension introspection: " + method)
	var config := ConfigFile.new()
	if config.load(path) != OK:
		return fail("INVALID_CONFIGURATION", "Cannot read the extension declaration")
	if not bool(config.get_value("configuration", "reloadable", false)) or not bool(extension.call("is_reloadable")):
		return fail("NOT_RELOADABLE", "Enable reloadable and binding support, then restart the editor once to load that configuration", {"requires_restart": true})
	var library_path := str(extension.call("get_library_path"))
	if library_path.is_empty() or not FileAccess.file_exists(library_path):
		return fail("LIBRARY_NOT_FOUND", "The selected native library is unavailable")
	var library_hash := FileAccess.get_sha256(library_path)
	if args.has("expected_library_hash") and str(args.expected_library_hash) != library_hash:
		return fail("LIBRARY_CONFLICT", "The compiled library differs from expected_library_hash")
	var before := snapshot(extension)
	if not before.ok:
		return before
	var logs_before: Dictionary = EditorInterface.get_editor_log(0, 1)
	var status := GDExtensionManager.reload_extension(path)
	var details := {"path": path, "native_status": status, "reloaded": status == GDExtensionManager.LOAD_STATUS_OK, "verified": false, "requires_restart": status == GDExtensionManager.LOAD_STATUS_NEEDS_RESTART}
	if status != GDExtensionManager.LOAD_STATUS_OK:
		return fail("NEEDS_RESTART" if status == GDExtensionManager.LOAD_STATUS_NEEDS_RESTART else "RELOAD_FAILED", "Native extension reload failed; inspect diagnostics before retrying", details)
	var reload_state: Dictionary = GDExtensionManager.call("get_reload_state")
	return {"ok": true, "result": {"path": path, "library_path": library_path, "library_hash": library_hash, "before": before, "args": args, "state": {"editor_pid": state.editor_pid, "editor_epoch": state.editor_epoch}, "logs_cursor": logs_before.latest_cursor, "reload_revision": reload_state.requested_revision}}

func complete(ticket: Dictionary, current: Dictionary) -> Dictionary:
	var state: Dictionary = ticket.state
	if current.editor_epoch != state.editor_epoch:
		return fail("EDITOR_CONFLICT", "Editor changed while native reload was completing")
	var reload_state: Dictionary = GDExtensionManager.call("get_reload_state")
	if int(reload_state.completed_revision) < int(ticket.reload_revision) or not bool(EditorInterface.call("is_extension_reload_complete", int(ticket.reload_revision))):
		return {"ok": true, "result": {"pending": true}}
	var path := str(ticket.path)
	var library_path := str(ticket.library_path)
	var library_hash := str(ticket.library_hash)
	var before: Dictionary = ticket.before
	var args: Dictionary = ticket.args
	var extension := GDExtensionManager.get_extension(path)
	var status := GDExtensionManager.LOAD_STATUS_OK
	var details := {"path": path, "native_status": status, "reloaded": true, "verified": false, "requires_restart": false}
	if extension == null or not extension.is_library_open():
		return fail("RELOAD_VALIDATION_FAILED", "The extension was unloaded before validation", details)
	var after := snapshot(extension)
	if not after.ok:
		return fail("RELOAD_VALIDATION_FAILED", after.error.message, details)
	if FileAccess.get_sha256(library_path) != library_hash:
		return fail("LIBRARY_CHANGED_DURING_RELOAD", "The native library changed during reload; do not replay the operation", details)
	var expected: Dictionary = args.get("expected_classes", {})
	for name in after.classes:
		if before.classes.has(name) and not before.classes[name].methods.is_empty() and after.classes[name].methods.is_empty() and not expected.has(name):
			return fail("RELOAD_VALIDATION_FAILED", "Native method registration disappeared: " + name, details)
	if not before.classes.is_empty() and after.classes.is_empty() and not args.has("expected_classes"):
		return fail("RELOAD_VALIDATION_FAILED", "All extension classes disappeared", details)
	for name in expected:
		if not after.classes.has(name):
			return fail("RELOAD_VALIDATION_FAILED", "Expected extension class is missing: " + name, details)
		for method in expected[name]:
			if method not in after.classes[name].methods:
				return fail("RELOAD_VALIDATION_FAILED", "Expected native method is missing: %s.%s" % [name, method], details)
	var logs: Dictionary = EditorInterface.get_editor_log(int(ticket.logs_cursor), 2048)
	var errors: Array = []
	for entry in logs.entries:
		if int(entry.type) == 1:
			errors.append(entry)
	if not errors.is_empty() or logs.dropped:
		details["errors"] = errors
		details["diagnostics_dropped"] = logs.dropped
		return fail("RELOAD_VALIDATION_FAILED", "Native reload or editor refresh reported errors; inspect diagnostics before retrying", details)
	var added: Array = []
	var removed: Array = []
	for name in after.classes:
		if not before.classes.has(name):
			added.append(name)
	for name in before.classes:
		if not after.classes.has(name):
			removed.append(name)
	return {"ok": true, "result": {"pending": false, "path": path, "reloaded": true, "verified": true, "native_status": status, "requires_restart": false, "library_path": library_path, "library_hash": library_hash, "classes": after.classes, "added_classes": added, "removed_classes": removed, "editor_pid": state.editor_pid, "editor_epoch": state.editor_epoch, "reload_revision": ticket.reload_revision, "runtime_updated": false}}
