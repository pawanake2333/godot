@tool
extends RefCounted

const Safety = preload("res://addons/godot_mcp/contract/editor_safety.gd")

var codec: RefCounted
var plugin: EditorPlugin

func _init(values: RefCounted, editor_plugin: EditorPlugin) -> void:
	codec = values
	plugin = editor_plugin

func fail(code: String, message: String) -> Dictionary:
	return {"ok": false, "error": {"code": code, "message": message}}

func settings_revision() -> String:
	var entries: Array[String] = []
	for info in ProjectSettings.get_property_list():
		var key := str(info.name)
		if ProjectSettings.has_setting(key):
			entries.append(key + "=" + var_to_str(ProjectSettings.get_setting(key)))
	entries.sort()
	return "\n".join(entries).sha256_text()

func settings_snapshot(keys: Array) -> Dictionary:
	var result := {}
	for key in keys:
		var exists := ProjectSettings.has_setting(key)
		result[key] = {"exists": exists, "value": codec.encode(ProjectSettings.get_setting(key)) if exists else null}
	return {"ok": true, "result": {"settings": result, "settings_revision": settings_revision(), "disk_hash": FileAccess.get_sha256("res://project.godot")}}

func invoke(name: String, args: Dictionary) -> Dictionary:
	match name:
		"godot_project_settings_set":
			var before := settings_revision()
			if str(args.expected_revision) != before:
				return fail("REVISION_CONFLICT", "Project settings changed; read the current settings revision")
			if bool(args.get("save", false)) and (not args.has("expected_disk_hash") or str(args.expected_disk_hash) != FileAccess.get_sha256("res://project.godot")):
				return fail("DISK_CONFLICT", "Saving requires the current project.godot disk hash")
			var infos := {}
			for info in ProjectSettings.get_property_list():
				infos[str(info.name)] = info
			var changed: Array = []
			for key in args.changes:
				# The native General inspector hides settings owned by other editor
				# models (InputMap, plugin registry, autoload instances, imports...).
				if infos.has(key) and (not int(infos[key].usage) & PROPERTY_USAGE_EDITOR or int(infos[key].usage) & (PROPERTY_USAGE_INTERNAL | PROPERTY_USAGE_READ_ONLY)):
					return fail("NATIVE_EDITOR_REQUIRED", "This setting is not editable in the native General inspector")
				for prefix in ["input/", "autoload/", "editor_plugins/", "editor_settings/", "shader_globals/", "file_customization/", "importer_defaults/", "global_group/", "_"]:
					if str(key).begins_with(prefix):
						return fail("NATIVE_EDITOR_REQUIRED", "This setting belongs to a specialized editor; use that editor or edit the project offline")
				var parsed: Dictionary = codec.decode_property(args.changes[key], infos[key]) if infos.has(key) and args.changes[key] != null else codec.decode(args.changes[key])
				if not parsed.ok:
					return fail("INVALID_VALUE", parsed.error)
				var scope_error := Safety.validate_value(parsed.value, null)
				if not scope_error.is_empty():
					return fail("INVALID_REFERENCE_SCOPE", scope_error)
				changed.append({"key": key, "value": parsed.value, "previous": ProjectSettings.get_setting(key) if ProjectSettings.has_setting(key) else null, "existed": ProjectSettings.has_setting(key)})
			var undo := plugin.get_undo_redo()
			undo.create_action("MCP: Project settings", UndoRedo.MERGE_DISABLE, ProjectSettings, false, false)
			undo.force_fixed_history()
			for entry in changed:
				undo.add_do_property(ProjectSettings, entry.key, entry.value)
				undo.add_undo_property(ProjectSettings, entry.key, entry.previous if entry.existed else null)
			undo.commit_action()
			var error := ProjectSettings.save() if bool(args.get("save", false)) else OK
			if error != OK:
				undo.call("undo_history", EditorUndoRedoManager.GLOBAL_HISTORY)
				return fail("SAVE_FAILED", error_string(error))
			return {"ok": true, "result": {"applied": true, "saved": bool(args.get("save", false)), "undoable": true, "keys": args.changes.keys(), "restart_requirements": "depends_on_setting", "settings_revision_before": before, "settings_revision": settings_revision(), "disk_hash": FileAccess.get_sha256("res://project.godot")}}
		"godot_editor_select":
			if args.has("panel"):
				EditorInterface.set_main_screen_editor({"2d": "2D", "3d": "3D", "script": "Script", "game": "Game"}[args.panel])
				return {"ok": true, "result": {"selected_panel": args.panel}}
			var root := EditorInterface.get_edited_scene_root()
			var nodes: Array = []
			for path in args.get("node_paths", []):
				var node: Node = root.get_node_or_null(NodePath(path)) if root != null else null
				if node == null or (node != root and not root.is_ancestor_of(node)):
					return fail("OBJECT_NOT_FOUND", path)
				nodes.append(node)
			var selection := EditorInterface.get_selection()
			selection.clear()
			for node in nodes:
				selection.add_node(node)
			return {"ok": true, "result": {"selected_nodes": nodes.size()}}

	return fail("TOOL_NOT_FOUND", name)
