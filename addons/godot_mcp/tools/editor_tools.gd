@tool
extends RefCounted

const Safety = preload("res://addons/godot_mcp/contract/editor_safety.gd")

const ProjectTools = preload("res://addons/godot_mcp/tools/project_tools.gd")
var project_tools: RefCounted
const ScriptDocuments = preload("res://addons/godot_mcp/tools/script_documents.gd")
var script_documents: RefCounted
const TreeSnapshots = preload("res://addons/godot_mcp/contract/tree_snapshots.gd")
var tree_snapshots := TreeSnapshots.new()

const Animations = preload("res://addons/godot_mcp/tools/animation_tools.gd")
const Reflection = preload("res://addons/godot_mcp/contract/reflection.gd")
var animations: RefCounted

const Structure = preload("res://addons/godot_mcp/tools/structure.gd")
var structure: RefCounted

const Values = preload("res://addons/godot_mcp/contract/values.gd")
var values := Values.new()
var plugin: EditorPlugin
var editable_resources := {}

func _init(owner_plugin: EditorPlugin) -> void:
	plugin = owner_plugin
	structure = Structure.new(plugin, values)
	animations = Animations.new(values)
	project_tools = ProjectTools.new(values, plugin)
	script_documents = ScriptDocuments.new(values)

func ok(data: Variant) -> Dictionary:
	return {"ok": true, "result": data}

func fail(code: String, message: String) -> Dictionary:
	return {"ok": false, "error": {"code": code, "message": message}}

func revision(_root: Node) -> int:
	return int(plugin.history_revision)

func scene_root(args: Dictionary) -> Node:
	if not args.has("scene_id"):
		return EditorInterface.get_edited_scene_root()
	var requested: Object = values.resolve(str(args.scene_id))
	return requested as Node if requested in EditorInterface.get_open_scene_roots() else null

func target(args: Dictionary):
	var root := scene_root(args)
	if args.has("object_ref"):
		var object: Object = values.resolve(str(args.object_ref))
		if object is Node and (root == null or (object != root and not root.is_ancestor_of(object))):
			return null
		return object
	if root == null:
		return null
	var node := root.get_node_or_null(NodePath(str(args.get("node_path", "."))))
	return node if node == root or root.is_ancestor_of(node) else null

func invoke(name: String, args: Dictionary) -> Dictionary:
	var root := scene_root(args)
	if plugin.get_undo_redo().is_committing_action():
		return fail("EDITOR_BUSY", "An editor Undo action is being committed")
	if name == "godot_edit_batch" and root != EditorInterface.get_edited_scene_root():
		return fail("DOCUMENT_INACTIVE", "Activate the document before structural edits")
	if name in ["godot_script_read", "godot_script_patch"]:
		return script_documents.invoke(name, args)
	if args.has("scene_id"):
		if root == null:
			return fail("SCENE_CONFLICT", "Target scene document is closed or its reference expired")
		if args.has("expected_revision") and int(args.expected_revision) != revision(root):
			return fail("REVISION_CONFLICT", "Scene history changed; inspect before editing")
		if name in ["godot_scene_save", "godot_scene_close", "godot_editor_select"] or (name == "godot_object_call" and bool(args.get("allow_mutation", false))):
			if root != EditorInterface.get_edited_scene_root():
				return fail("DOCUMENT_INACTIVE", "Activate this document with scene_open before saving, closing, selecting or invoking side effects")
	if name == "godot_edit_batch":
		var response: Dictionary = structure.execute(root, args.operations, bool(args.get("dry_run", false)))
		if response.ok:
			response.result["revision"] = revision(root)
		return response
	if name in ["godot_project_settings_set", "godot_editor_select"]:
		return project_tools.invoke(name, args)
	if name in ["godot_animation_list", "godot_animation_inspect", "godot_animation_sample"]:
		return animations.read(name, target(args), args)
	if name == "godot_animation_edit":
		if root != EditorInterface.get_edited_scene_root():
			return fail("DOCUMENT_INACTIVE", "Activate the document before animation editing")
		var animation: Animation = animations.resolve_animation(target(args), str(args.get("animation_name", "")))
		if animation == null:
			return fail("ANIMATION_NOT_FOUND", "Expected an Animation reference or player animation")
		var path := animation.resource_path.split("::")[0]
		if not path.is_empty() and path.get_extension() not in ["tscn", "tres", "res"]:
			return fail("IMPORTED_RESOURCE", "Duplicate imported animation before editing")
		if target(args) is Node:
			approve_resource(animation)
		if not resource_editable(animation):
			return fail("READ_ONLY_RESOURCE", "Animation is not a project/document resource")
		var edited: Dictionary = animations.edited_copy(animation, args)
		if not edited.ok:
			return edited
		var undo := plugin.get_undo_redo()
		undo.create_action("MCP: Edit animation", UndoRedo.MERGE_DISABLE, animation)
		undo.force_fixed_history()
		undo.add_do_method(animations, "restore", animation, edited.copy)
		undo.add_undo_method(animations, "restore", animation, animation.duplicate())
		undo.add_do_method(animation, "emit_changed")
		undo.add_undo_method(animation, "emit_changed")
		undo.commit_action()
		return ok({"applied": true, "undoable": true, "saved": false, "track": edited.track, "revision": revision(root)})
	if name == "godot_object_call":
		var object: Object = target(args)
		# Two typed library operations have explicit native inverses. Every other
		# editor method must be const; allow_mutation is only unrestricted at runtime.
		if bool(args.get("allow_mutation", false)) and ((object is AnimationLibrary and str(args.method) == "add_animation") or (object is AnimationPlayer and str(args.method) == "add_animation_library")):
			return add_animation_resource(object, args, root)
		if str(args.method).begins_with("_"):
			return fail("METHOD_NOT_ALLOWED", "Editor internal callbacks cannot be invoked")
		var safe_args := args.duplicate()
		safe_args["allow_mutation"] = false
		return Reflection.invoke(object, safe_args, values)

	match name:
		"godot_scene_create":
			var type := str(args.root_type)
			var node_name := str(args.root_name)
			if not EditorInterface.is_scene_class(type) or not ClassDB.is_parent_class(type, "Node") or not ClassDB.can_instantiate(type):
				return fail("INVALID_CLASS", type)
			if node_name.is_empty() or node_name != node_name.validate_node_name():
				return fail("INVALID_NAME", node_name)
			if not EditorInterface.has_method("new_scene"):
				return fail("CAPABILITY_UNAVAILABLE", "Build editor with new_scene API")
			var node: Node = ClassDB.instantiate(type)
			var reference := values.retain_object(node)
			if not reference.has("ref"):
				node.free()
				return fail("REFERENCE_BUDGET", "Release object references before creating a document")
			node.name = node_name
			EditorInterface.call("new_scene")
			EditorInterface.add_root_node(node)
			return ok({"scene_id": reference.ref, "revision": revision(node), "saved": false})
		"godot_scene_open":
			var path := str(args.path)
			if not path.begins_with("res://") or not ResourceLoader.exists(path, "PackedScene"):
				return fail("SCENE_NOT_FOUND", path)
			EditorInterface.open_scene_from_path(path)
			var opened := EditorInterface.get_edited_scene_root()
			if opened == null or opened.scene_file_path != path:
				return fail("OPEN_FAILED", path)
			var reference := values.retain_object(opened)
			if not reference.has("ref"):
				return fail("REFERENCE_BUDGET", "Document opened; release object references then use scene_list")
			return ok({"scene_id": reference.ref, "revision": revision(opened)})
		"godot_scene_save":
			if root == null:
				return fail("SCENE_NOT_FOUND", "No active document")
			var path := str(args.get("path", root.scene_file_path)).simplify_path()
			for document in EditorInterface.get_open_scene_roots():
				if document != root and document.scene_file_path == path:
					return fail("DOCUMENT_PATH_CONFLICT", "Another open document owns this scene path")
			if not path.begins_with("res://") or path.contains("..") or not path.ends_with(".tscn"):
				return fail("INVALID_PATH", "Expected project .tscn path")
			if path != root.scene_file_path and FileAccess.file_exists(path) and not bool(args.get("overwrite", false)):
				return fail("ALREADY_EXISTS", path)
			EditorInterface.save_scene_as(path, false)
			if root.scene_file_path != path or path in EditorInterface.get_unsaved_scenes() or not FileAccess.file_exists(path):
				return fail("SAVE_FAILED", path)
			return ok({"saved": true, "path": path, "hash": FileAccess.get_sha256(path), "revision": revision(root)})
		"godot_scene_close":
			var dirty := root.scene_file_path.is_empty() or root.scene_file_path in EditorInterface.get_unsaved_scenes()
			var policy := str(args.get("unsaved", "reject"))
			if dirty and policy == "reject":
				return fail("UNSAVED_CHANGES", "Save the document or explicitly discard it")
			if dirty and policy == "save":
				if root.scene_file_path.is_empty():
					return fail("PATH_REQUIRED", "Save the new document to a path first")
				var error := EditorInterface.save_scene()
				if error != OK or root.scene_file_path in EditorInterface.get_unsaved_scenes():
					return fail("SAVE_FAILED", root.scene_file_path)
			var error := EditorInterface.close_scene()
			return ok({"closed": true}) if error == OK else fail("CLOSE_FAILED", error_string(error))
		"godot_scene_undo", "godot_scene_redo":
			var manager := plugin.get_undo_redo()
			var history := manager.get_history_undo_redo(manager.get_object_history_id(root))
			var active := root == EditorInterface.get_edited_scene_root()
			var available: bool = (manager.call("has_undo") if name == "godot_scene_undo" else manager.call("has_redo")) if active else ((history.has_undo() if name == "godot_scene_undo" else history.has_redo()) if history != null else false)
			if not available:
				return fail("EMPTY_HISTORY", "No action available")
			if name == "godot_scene_undo":
				manager.call("undo") if active else manager.call("undo_history", manager.get_object_history_id(root))
			else:
				manager.call("redo") if active else manager.call("redo_history", manager.get_object_history_id(root))
			return ok({"revision": revision(root), "applied": true, "saved": false})
		"godot_scene_list":
			var scenes: Array = []
			for document in EditorInterface.get_open_scene_roots():
				if not values.retain_object(document).has("ref"):
					return fail("REFERENCE_BUDGET", "Release object references before listing documents")
				scenes.append({"scene_id": values.retain_object(document).get("ref", ""), "revision": revision(document), "path": document.scene_file_path, "active": document == root, "dirty": document.scene_file_path.is_empty() or document.scene_file_path in EditorInterface.get_unsaved_scenes()})
			return ok({"scenes": scenes, "active": {"scene_id": values.retain_object(root).get("ref", ""), "revision": revision(root), "path": root.scene_file_path} if root != null else null})
		"godot_class_describe":
			var type := str(args.class_name)
			if not ClassDB.class_exists(type):
				return fail("CLASS_NOT_FOUND", type)
			return ok({"class": type, "properties": ClassDB.class_get_property_list(type), "methods": ClassDB.class_get_method_list(type), "signals": ClassDB.class_get_signal_list(type)})
		"godot_resource_load":
			var path := str(args.path)
			if not path.begins_with("res://") or not ResourceLoader.exists(path):
				return fail("RESOURCE_NOT_FOUND", path)
			var resource := load(path)
			if resource == null:
				return fail("LOAD_FAILED", path)
			approve_resource(resource)
			return ok({"resource": values.retain_object(resource), "path": resource.resource_path})
		"godot_resource_create":
			var type := str(args.class_name)
			if not EditorInterface.is_scene_class(type) or not ClassDB.is_parent_class(type, "Resource") or not ClassDB.can_instantiate(type):
				return fail("INVALID_CLASS", "Class must be an instantiable Resource")
			var resource: Resource = ClassDB.instantiate(type)
			approve_resource(resource)
			return ok({"resource": values.retain_object(resource), "saved": false})
		"godot_resource_duplicate":
			var resource = values.resolve(str(args.object_ref))
			if not resource is Resource:
				return fail("OBJECT_NOT_FOUND", "Resource reference expired")
			var copy: Resource = resource.duplicate(bool(args.get("deep", false)))
			approve_resource(copy)
			return ok({"resource": values.retain_object(copy), "saved": false, "resource_policy": "duplicate_subresources" if bool(args.get("deep", false)) else "share_subresources", "warnings": ["External resource references and custom Resource duplicate behavior follow Godot rules; inspect the copy before mutation"]})
		"godot_resource_save":
			var resource = values.resolve(str(args.object_ref))
			var path := str(args.path).simplify_path()
			if not resource_editable(resource):
				return fail("READ_ONLY_RESOURCE", "Resource is not an editable project/document resource")
			if not resource is Resource or not path.begins_with("res://") or path.contains(".."):
				return fail("INVALID_ARGUMENT", "Expected live Resource and project path")
			if FileAccess.file_exists(path) and not bool(args.get("overwrite", false)):
				return fail("ALREADY_EXISTS", path)
			if FileAccess.file_exists(path) and str(args.get("expected_disk_hash", "")) != FileAccess.get_sha256(path):
				return fail("DISK_CONFLICT", "Existing resource saves require its current expected_disk_hash")
			if resource is Script or resource is Shader or resource is PackedScene or resource is EditorSettings or path.get_extension() not in ["tres", "res"]:
				return fail("DOCUMENT_SAVE_REQUIRED", "Use the owning document editor to save scripts, shaders, scenes and editor settings")
			if not resource.resource_path.is_empty() and (resource.resource_path.contains("::") or resource.resource_path != path):
				return fail("RESOURCE_IDENTITY_CONFLICT", "Duplicate a resource before saving under a different path")
			var cached := ResourceLoader.get_cached_ref(path)
			if cached != null and cached != resource:
				return fail("RESOURCE_IDENTITY_CONFLICT", "Another loaded resource owns the destination path")
			var error: int = EditorInterface.save_resource_in_path(resource, path)
			return ok({"saved": true, "path": path, "hash": FileAccess.get_sha256(path), "uid": str(ResourceLoader.get_resource_uid(path))}) if error == OK else fail("SAVE_FAILED", error_string(error))
		"godot_object_release":
			return ok({"released": values.release(args.object_refs)})
		"godot_object_inspect":
			var object: Object = target(args)
			if object == null:
				return fail("OBJECT_NOT_FOUND", "Object was freed or is outside target scene")
			var properties: Array = []
			var requested: Array = args.get("properties", [])
			for info in object.get_property_list():
				if properties.size() >= int(args.get("max_properties", 128)):
					break
				if info.usage & (PROPERTY_USAGE_CATEGORY | PROPERTY_USAGE_GROUP | PROPERTY_USAGE_SUBGROUP):
					continue
				if requested.is_empty() or str(info.name) in requested:
					if int(info.usage) & PROPERTY_USAGE_STORAGE and (object is Node or resource_editable(object)):
						approve_resource(object.get(info.name))
					properties.append({"name": str(info.name), "type": info.type, "value": values.encode(object.get(info.name))})
			return ok({"object": values.retain_object(object), "properties": properties, "revision": revision(root) if root != null else null})
		"godot_object_set_properties":
			return set_properties(args, root)
		"godot_project_settings_get":
			return project_tools.settings_snapshot(args["keys"])
		"godot_script_reload":
			var opened_paths: Array = []
			for script in EditorInterface.get_script_editor().get_open_scripts():
				opened_paths.append(script.resource_path)
			for path in args.paths:
				if path in opened_paths:
					return fail("OPEN_SCRIPT", "Close editor script documents before explicit reload")
				if not str(path).begins_with("res://") or not ResourceLoader.exists(path, "Script"):
					return fail("SCRIPT_NOT_FOUND", path)
			var results: Array = []
			for path in args.paths:
				var script: Script = load(path)
				if script.is_tool():
					return fail("LIVE_TOOL_SCRIPT", "Reload tool scripts through the native script editor to preserve live editor instances")
				var error := script.reload(true)
				results.append({"path": path, "error": error, "reloaded": error == OK})
			return ok({"scripts": results, "runtime_updated": false})
		"godot_resource_reimport":
			var filesystem := EditorInterface.get_resource_filesystem()
			if filesystem.is_scanning():
				return fail("BUSY", "Filesystem scan in progress")
			for path in args.paths:
				if not str(path).begins_with("res://") or not FileAccess.file_exists(path):
					return fail("FILE_NOT_FOUND", path)
				if not FileAccess.file_exists(str(path) + ".import"):
					return fail("NO_IMPORTER", "Scan/import the source first; this path has no importer metadata: " + str(path))
			var before: Dictionary = EditorInterface.get_editor_log(0, 1)
			filesystem.reimport_files(PackedStringArray(args.paths))
			var logs := EditorInterface.get_editor_log(int(before.latest_cursor), 2048)
			var errors: Array = []
			for entry in logs.entries:
				if int(entry.type) == 1:
					errors.append(entry)
			var resources: Array = []
			var all_loaded := true
			for path in args.paths:
				var resource: Resource = ResourceLoader.load(path, "", ResourceLoader.CACHE_MODE_IGNORE)
				resources.append({"path": path, "loaded": resource != null, "class": resource.get_class() if resource != null else "", "uid": str(ResourceLoader.get_resource_uid(path))})
				all_loaded = all_loaded and resource != null
			return ok({"completed": true, "paths": args.paths, "resources": resources, "errors_since_start": errors, "diagnostics_dropped": logs.dropped, "success": all_loaded and errors.is_empty() and not logs.dropped})
		"godot_scene_tree", "godot_object_query":
			if root == null:
				return fail("SCENE_NOT_FOUND", "No selected scene document")
			return tree_snapshots.read(root, args, str(revision(root)), values, false)
		"godot_filesystem_scan":
			var filesystem := EditorInterface.get_resource_filesystem()
			if filesystem.is_scanning():
				await filesystem.filesystem_changed
			filesystem.scan()
			await filesystem.filesystem_changed
			return ok({"scanned": true, "scanning": filesystem.is_scanning()})

	return fail("TOOL_NOT_FOUND", name)

func set_properties(args: Dictionary, root: Node) -> Dictionary:
	var object: Object = target(args)
	if object == null or root == null:
		return fail("OBJECT_NOT_FOUND", "Live object and editing scene required")
	if object is Node and object != root and object.owner != root:
		return fail("NODE_NOT_EDITABLE", "Internal/foreign scene nodes cannot be modified")
	if object is Resource and not resource_editable(object):
		return fail("READ_ONLY_RESOURCE", "Only project/document resources and explicit resource copies can be modified")
	if not object is Node and not object is Resource:
		return fail("READ_ONLY_OBJECT", "Editor infrastructure objects cannot be modified")
	if object is Script or object is Shader or object is PackedScene or object is EditorSettings:
		return fail("DOCUMENT_EDIT_REQUIRED", "Use the owning document editor for this resource")
	if object is Resource and not object.resource_path.is_empty() and object.resource_path.split("::")[0].get_extension() not in ["tscn", "scn", "tres", "res"]:
		return fail("IMPORTED_RESOURCE", "Duplicate imported resources before editing their properties")
	var prepared := {}
	var infos := {}
	for info in object.get_property_list():
		infos[str(info.name)] = info
	for key in args.changes:
		if not Safety.editable_property(str(key), infos.get(key, {})):
			return fail("PROPERTY_NOT_FOUND", str(key))
		var parsed := values.decode_property(args.changes[key], infos[key])
		if not parsed.ok:
			return fail("INVALID_VALUE", parsed.error)
		var scope_error := Safety.validate_value(parsed.value, root)
		if not scope_error.is_empty():
			return fail("INVALID_REFERENCE_SCOPE", scope_error)
		prepared[key] = parsed.value
	var error: int = EditorInterface.edit_inspector_properties(object, prepared)
	if error != OK:
		return fail("INSPECTOR_EDIT_FAILED", error_string(error))
	return ok({"applied": true, "undoable": true, "saved": false, "persistable": "unknown", "revision": revision(root), "resource_policy": "shared" if object is Resource else "node_properties", "affected_resource": object.resource_path if object is Resource else "", "warnings": ["Shared Resource changes affect every user of that Resource; external resources require resource_save"] if object is Resource else []})


func add_animation_resource(object: Object, args: Dictionary, root: Node) -> Dictionary:
	if object is Node and object != root and object.owner != root:
		return fail("NODE_NOT_EDITABLE", "Animation player belongs to a foreign scene")
	if object is Resource and not resource_editable(object):
		return fail("READ_ONLY_RESOURCE", "Animation library is not an editable project/document resource")
	var arguments: Array = args.get("arguments", [])
	if arguments.size() != 2:
		return fail("INVALID_ARGUMENT", "Expected library/animation name and resource")
	var name_value: Dictionary = values.decode(arguments[0])
	var resource_value: Dictionary = values.decode(arguments[1])
	if not name_value.ok or not resource_value.ok or not (name_value.value is String or name_value.value is StringName):
		return fail("INVALID_ARGUMENT", "Invalid library/animation arguments")
	var key := str(name_value.value)
	var library_operation := object is AnimationPlayer
	if (library_operation and not resource_value.value is AnimationLibrary) or (not library_operation and not resource_value.value is Animation):
		return fail("INVALID_ARGUMENT", "Unexpected animation resource type")
	if key.contains("/") or key.contains(":") or key.contains(",") or key.contains("[") or (not library_operation and key.is_empty()):
		return fail("INVALID_ARGUMENT", "Invalid animation/library name")
	if object.call("has_animation_library" if library_operation else "has_animation", key):
		return fail("ALREADY_EXISTS", "Read the current library before replacing an animation")
	if object is Resource and not object.resource_path.is_empty() and object.resource_path.split("::")[0].get_extension() not in ["tscn", "tres", "res"]:
		return fail("IMPORTED_RESOURCE", "Duplicate imported animation libraries before editing")
	var undo := plugin.get_undo_redo()
	undo.create_action("MCP: Add animation resource", UndoRedo.MERGE_DISABLE, object)
	undo.force_fixed_history()
	undo.add_do_method(object, str(args.method), StringName(key), resource_value.value)
	undo.add_undo_method(object, "remove_animation_library" if library_operation else "remove_animation", StringName(key))
	undo.commit_action()
	return ok({"value": OK, "mutating": true, "undoable": true, "saved": false, "revision": revision(root)})

func resource_editable(object: Object) -> bool:
	return object is Resource and not EditorInterface.is_resource_read_only(object) and (editable_resources.has(object.get_instance_id()) or object.resource_path.begins_with("res://"))

func approve_resource(value: Variant, budget: int = 256) -> void:
	var pending: Array = [value]
	while not pending.is_empty() and budget > 0:
		budget -= 1
		var current: Variant = pending.pop_back()
		if current is Resource:
			editable_resources[current.get_instance_id()] = true
		elif current is Array:
			pending.append_array(current.slice(0, budget))
		elif current is Dictionary:
			for key in current.keys().slice(0, budget):
				pending.append(current[key])
