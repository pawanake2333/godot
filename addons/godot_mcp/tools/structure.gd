@tool
extends RefCounted

const Safety = preload("res://addons/godot_mcp/contract/editor_safety.gd")

var plugin: EditorPlugin
var values: RefCounted

func _init(editor_plugin: EditorPlugin, codec: RefCounted) -> void:
	plugin = editor_plugin
	values = codec

func error(message: String) -> Dictionary:
	return {"ok": false, "error": {"code": "INVALID_EDIT", "message": message}}

func inherited_node(root: Node, node: Node) -> bool:
	if node != root and not root.is_ancestor_of(node):
		# A newly created batch placeholder has no parent until the Undo action commits.
		return false
	return EditorInterface.is_scene_node_inherited(root, node)

func nodes(root: Node) -> Dictionary:
	var found := {".": root}
	var queue: Array[Node] = [root]
	while not queue.is_empty():
		var node := queue.pop_front() as Node
		for child in node.get_children():
			if found.size() >= 4096:
				return {}
			found[str(root.get_path_to(child))] = child
			queue.append(child)
	return found

func execute(root: Node, operations: Array, dry_run: bool) -> Dictionary:
	var path_edits := operations.any(func(op): return op.op in ["rename", "reparent", "delete"])
	if path_edits:
		for op in operations:
			if op.op == "attach":
				return error("Split script attachment and path restructuring into separate batches")
			if op.op == "set":
				for key in op.changes:
					var value: Variant = op.changes[key]
					if value is Array or value is Dictionary or value == null:
						# Typed scalar/vector tags are safe; object refs, NodePaths and
						# containers may overlap native reference rewriting.
						if not value is Dictionary or str(value.get("_type", "")) in ["", "NodePath", "ObjectRef", "Resource", "Array", "Dictionary"] or value.has("ref"):
							return error("Split reference/container property assignments and path restructuring into separate batches")
	var lookup := nodes(root)
	var original_lookup := lookup.duplicate()
	if lookup.is_empty() or operations.is_empty() or operations.size() > 256:
		return error("Batch requires 1..256 operations and at most 4096 scene nodes")
	var forward: Array = []
	var backward: Array = []
	var created: Array[Node] = []
	var removed: Array[Node] = []
	var virtual_parents := {}
	var virtual_names := {}
	var state := {"aliases": {}, "properties": {}, "scripts": {}, "groups": {}, "connections": {}}
	var results: Array = []
	for index in range(operations.size()):
		var operation: Dictionary = operations[index].duplicate(true)
		for field in ["node_path", "parent_path", "receiver_path"]:
			if operation.has(field):
				operation[field] = resolve_path(str(operation[field]), lookup, state.aliases)
		var alias := str(operation.get("id", ""))
		var planned := error("Invalid or repeated temporary id") if (not alias.is_empty() and (not alias.is_valid_identifier() or state.aliases.has(alias) or operation.op not in ["create", "instance_scene", "duplicate"])) else plan(root, lookup, virtual_parents, virtual_names, operation, created, removed, dry_run, state)
		if not planned.ok:
			for node in created:
				if is_instance_valid(node) and node.get_parent() == null:
					node.free()
			planned.error["operation_index"] = index
			return planned
		if not alias.is_empty():
			state.aliases[alias] = lookup[planned.data.node_path]
		forward.append_array(planned.forward)
		var inverse: Array = planned.backward
		inverse.append_array(backward)
		backward = inverse
		results.append(planned.get("data", {}))
	if dry_run:
		for node in created:
			if is_instance_valid(node) and node.get_parent() == null:
				node.free()
		return {"ok": true, "result": {"applied": false, "dry_run": true, "saved": false, "undoable": true, "atomic": false, "operations": results}}
	if forward.is_empty():
		return {"ok": true, "result": {"applied": false, "saved": false, "undoable": true, "operations": results}}
	var undo := plugin.get_undo_redo()
	undo.create_action("MCP: Batch edit", UndoRedo.MERGE_DISABLE, root)
	undo.force_fixed_history()
	record_steps(undo, forward, false)
	record_steps(undo, backward, true)
	var property_changes := {}
	for step in forward:
		if step.kind == "property" and step.key != "name":
			if not property_changes.has(step.object):
				property_changes[step.object] = {}
			property_changes[step.object][step.key] = step.value
	for object in property_changes:
		for key in property_changes[object]:
			EditorInterface.add_undo_redo_property_change(object, key, property_changes[object][key], false)
	var final_paths := {}
	for path in original_lookup:
		var node: Node = original_lookup[path]
		var final_path := NodePath("")
		for candidate in lookup:
			if lookup[candidate] == node:
				final_path = root.get_path() if candidate == "." else NodePath(str(root.get_path()).path_join(candidate))
				break
		if final_path != node.get_path():
			final_paths[node] = final_path
	if not final_paths.is_empty():
		EditorInterface.add_scene_node_path_changes(root, final_paths)
	if not removed.is_empty():
		undo.add_do_method(EditorInterface, "cleanup_scene_node_removal")
		undo.add_undo_method(EditorInterface, "cleanup_scene_node_removal")
	for node in created:
		undo.add_do_reference(node)
	for node in removed:
		undo.add_undo_reference(node)
	undo.commit_action()
	var references := {}
	for alias in state.aliases:
		var node: Node = state.aliases[alias]
		if node == root or root.is_ancestor_of(node):
			references[alias] = values.retain_object(node)
	return {"ok": true, "result": {"applied": true, "saved": false, "undoable": true, "atomic": false, "operations": results, "references": references}}

func resolve_path(path: String, lookup: Dictionary, aliases: Dictionary) -> String:
	if not path.begins_with("$"):
		return path
	var parts := path.split("/", false, 1)
	var node: Node = aliases.get(parts[0].trim_prefix("$"))
	if node == null:
		return "<invalid-temporary-reference>"
	for current in lookup:
		if lookup[current] == node:
			return str(current).path_join(parts[1]) if parts.size() > 1 else str(current)
	return "<deleted-temporary-reference>"

func plan(root: Node, lookup: Dictionary, parents: Dictionary, names: Dictionary, op: Dictionary, created: Array[Node], removed: Array[Node], dry_run: bool, state: Dictionary) -> Dictionary:
	var kind := str(op.get("op", ""))
	var path := str(op.get("node_path", "."))
	var node: Node = lookup.get(path)
	var forward: Array = []
	var backward: Array = []
	var result := {"op": kind}
	if kind in ["create", "instance_scene", "duplicate"]:
		var parent_path := str(op.get("parent_path", "."))
		var parent: Node = lookup.get(parent_path)
		var new_name := str(op.get("name", ""))
		var next_path := new_name if parent_path == "." else parent_path + "/" + new_name
		if parent == null or (parent != root and parent.owner != root and parent not in created) or new_name.is_empty() or new_name != new_name.validate_node_name() or lookup.has(next_path):
			return error("Parent/name is missing, not editable or already exists")
		var added: Node
		if kind == "create":
			var type := str(op.get("type", "Node"))
			if not EditorInterface.is_scene_class(type) or not ClassDB.is_parent_class(type, "Node") or not ClassDB.can_instantiate(type):
				return error("Invalid Node class")
			added = ClassDB.instantiate(type)
		elif kind == "instance_scene":
			var resource_path := str(op.get("path", ""))
			if not resource_path.begins_with("res://") or not ResourceLoader.exists(resource_path, "PackedScene"):
				return error("Scene resource does not exist")
			var packed: PackedScene = load(resource_path)
			added = ClassDB.instantiate(packed.get_state().get_node_type(0)) if dry_run else packed.instantiate(PackedScene.GEN_EDIT_STATE_INSTANCE)
		else:
			if node == null or node == root or node.owner != root:
				return error("Cannot duplicate this node")
			added = ClassDB.instantiate(node.get_class()) if dry_run else node.duplicate()
		if added == null:
			return error("Could not instantiate node")
		added.name = new_name
		lookup[next_path] = added
		parents[added] = parent
		names[added] = new_name
		created.append(added)
		for child_path in nodes(added):
			if child_path != ".":
				lookup[next_path.path_join(child_path)] = added.get_node(NodePath(child_path))
		var owners: Array = [[added, root]]
		if kind == "duplicate":
			for entry in collect_owners(node):
				if entry[0] != node and entry[1] == root:
					var cloned := added.get_node_or_null(node.get_path_to(entry[0]))
					if cloned != null:
						owners.append([cloned, root])
		forward.append({"kind": "add", "node": added, "parent": parent, "owners": owners})
		backward.append({"kind": "remove", "node": added, "parent": parent})
		result["node_path"] = next_path
	else:
		if node == null or (node != root and node.owner != root and node not in created):
			return error("Node does not belong to editable scene")
		if kind in ["rename", "reparent", "delete"]:
			if inherited_node(root, node):
				return error("Inherited nodes cannot be renamed, moved or deleted; edit the base scene")
			if node == root:
				return error("Cannot restructure root")
			var old_parent: Node = parents.get(node, node.get_parent())
			var old_name: StringName = names.get(node, node.name)
			var new_path := ""
			if kind == "rename":
				var name := str(op.get("name", ""))
				new_path = path.get_base_dir().path_join(name) if path.contains("/") else name
				if name.is_empty() or name != name.validate_node_name() or lookup.has(new_path):
					return error("Invalid or duplicate node name")
				forward.append({"kind": "property", "object": node, "key": "name", "value": name})
				backward.append({"kind": "property", "object": node, "key": "name", "value": old_name})
				names[node] = name
			elif kind == "reparent":
				if bool(op.get("keep_global_transform", true)) and node is Node3D and not node.is_inside_tree():
					return error("Global Node3D transforms require an active scene; activate it or use keep_global_transform=false")
				var destination := str(op.get("parent_path", "."))
				var parent: Node = lookup.get(destination)
				new_path = str(old_name) if destination == "." else destination + "/" + str(old_name)
				if parent == null or destination == path or destination.begins_with(path + "/") or lookup.has(new_path) or (parent != root and parent.owner != root and parent not in created):
					return error("Invalid reparent destination")
				forward.append({"kind": "reparent", "node": node, "parent": parent, "keep_global": bool(op.get("keep_global_transform", true)), "index": int(op.get("index", -1))})
				backward.append({"kind": "reparent", "node": node, "parent": old_parent, "keep_global": bool(op.get("keep_global_transform", true)), "index": node.get_index() if node.get_parent() != null else -1})
				# Reparenting controls/containers may change more than their parent.
				# Undo restores the exact local state captured before the batch.
				if node is Node3D or node is Node2D:
					backward.append({"kind": "property", "object": node, "key": "transform", "value": node.transform})
				elif node is Control:
					backward.append({"kind": "control_state", "node": node, "value": node.call("_edit_get_state")})
				parents[node] = parent
			else:
				forward.append({"kind": "remove", "node": node, "parent": old_parent})
				backward.append({"kind": "restore", "node": node, "parent": old_parent, "index": node.get_index() if node.get_parent() != null else -1, "owners": collect_owners(node)})
				removed.append(node)
			var replacements := {}
			for current_path in lookup.keys():
				if current_path == path or current_path.begins_with(path + "/"):
					if kind != "delete":
						replacements[new_path + current_path.trim_prefix(path)] = lookup[current_path]
					lookup.erase(current_path)
			lookup.merge(replacements)
			result["node_path"] = new_path
		elif kind == "set":
			if not state.properties.has(node):
				state.properties[node] = {}
			var infos := {}
			for info in node.get_property_list():
				infos[str(info.name)] = info
			var script: Script = state.scripts.get(node, node.get_script())
			var original_script: Script = node.get_script()
			if script != original_script and original_script != null:
				for info in original_script.get_script_property_list():
					infos.erase(str(info.name))
			if script != null:
				for info in script.get_script_property_list():
					infos[str(info.name)] = info
			for key in op.get("changes", {}):
				if not Safety.editable_property(str(key), infos.get(key, {})):
					return error("Invalid property: " + str(key))
				var decoded: Dictionary = values.decode_property(op.changes[key], infos[key])
				if not decoded.ok:
					return error(decoded.error)
				var previous: Variant
				if state.properties[node].has(key):
					previous = state.properties[node][key]
				elif script != null and script != node.get_script() and key in script.get_script_property_list().map(func(info): return str(info.name)):
					previous = script.get_property_default_value(key)
				else:
					previous = Safety.snapshot_value(node.get(key))
				var scope_error := Safety.validate_value(decoded.value, root)
				if not scope_error.is_empty():
					return error(scope_error)
				forward.append({"kind": "property", "object": node, "key": key, "value": decoded.value})
				backward.push_front({"kind": "property", "object": node, "key": key, "value": previous})
				state.properties[node][key] = decoded.value
		elif kind == "attach":
			var previous: Script = state.scripts.get(node, node.get_script())
			var script: Script
			if op.get("script_path") != null:
				var script_path := str(op.script_path)
				if not script_path.begins_with("res://") or not ResourceLoader.exists(script_path, "Script"):
					return error("Project script does not exist")
				script = load(script_path)
				if script == null or not node.is_class(script.get_instance_base_type()):
					return error("Script base type does not match the target node")
			if previous == script:
				return {"ok": true, "forward": [], "backward": [], "data": result}
			forward.append({"kind": "property", "object": node, "key": "script", "value": script})
			backward.append({"kind": "property", "object": node, "key": "script", "value": previous})
			if previous != null:
				for info in previous.get_script_property_list():
					if int(info.usage) & PROPERTY_USAGE_STORAGE and str(info.name) != "script":
						var previous_value: Variant = state.properties.get(node, {}).get(str(info.name), node.get(info.name) if previous == node.get_script() else previous.get_property_default_value(info.name))
						backward.append({"kind": "property", "object": node, "key": info.name, "value": previous_value})
			state.scripts[node] = script
			state.properties.erase(node)
		elif kind in ["connect", "disconnect"]:
			var receiver: Node = lookup.get(str(op.get("receiver_path", ".")))
			var signal_name := str(op.get("signal_name", ""))
			var method := str(op.get("method", ""))
			var source_script: Script = state.scripts.get(node, node.get_script())
			var receiver_script: Script = state.scripts.get(receiver, receiver.get_script() if receiver != null else null)
			var has_signal := node.has_signal(signal_name) or (source_script != null and signal_name in source_script.get_script_signal_list().map(func(info): return str(info.name)))
			var has_method := receiver != null and (receiver.has_method(method) or (receiver_script != null and method in receiver_script.get_script_method_list().map(func(info): return str(info.name))))
			if not has_signal or not has_method:
				return error("Signal or receiver method does not exist")
			var method_info := {}
			for info in receiver.get_method_list():
				if str(info.name) == method:
					method_info = info
			if receiver_script != null:
				for info in receiver_script.get_script_method_list():
					if str(info.name) == method:
						method_info = info
			var signal_arity := 0
			var signal_infos := node.get_signal_list()
			if source_script != null:
				signal_infos.append_array(source_script.get_script_signal_list())
			for info in signal_infos:
				if str(info.name) == signal_name:
					signal_arity = info.args.size()
			var unbind := int(op.get("unbind", 0))
			var incoming := signal_arity - unbind
			var supplied: int = incoming + op.get("binds", []).size()
			if incoming < 0 or supplied < method_info.args.size() - method_info.get("default_args", []).size() or (not int(method_info.flags) & METHOD_FLAG_VARARG and supplied > method_info.args.size()):
				return error("Signal arguments, unbind and binds do not match receiver method")
			var callback := Callable(receiver, method)
			var bindings: Array = []
			for value in op.get("binds", []):
				var argument_index: int = incoming + bindings.size()
				var decoded: Dictionary = values.decode_property(value, method_info.args[argument_index]) if argument_index < method_info.args.size() else values.decode(value)
				if not decoded.ok:
					return error(decoded.error)
				var scope_error := Safety.validate_value(decoded.value, root)
				if not scope_error.is_empty():
					return error(scope_error)
				bindings.append(decoded.value)
			callback = callback.bindv(bindings)
			# Callable adapters are applied from right to left: unbind signal arguments
			# first, then append the explicitly bound receiver arguments.
			if unbind > 0:
				callback = callback.unbind(unbind)
			if not state.connections.has(node):
				state.connections[node] = {}
			var connected := node.is_connected(signal_name, callback) if node.has_signal(signal_name) else false
			var flags := Object.CONNECT_PERSIST | int(op.get("flags", 0))
			for entry in node.get_signal_connection_list(signal_name) if node.has_signal(signal_name) else []:
				if entry.callable == callback:
					flags = int(entry.flags)
			var key: Array = [signal_name, callback]
			if state.connections[node].has(key):
				connected = state.connections[node][key].connected
				flags = state.connections[node][key].flags
			if (flags & Object.CONNECT_REFERENCE_COUNTED) != 0 or (connected and (flags & Object.CONNECT_PERSIST) == 0):
				return error("Runtime-owned or reference-counted connections cannot be edited")
			var connecting := kind == "connect"
			result["connected"] = connecting
			if connected != connecting:
				forward.append({"kind": "connection", "node": node, "signal_name": signal_name, "callback": callback, "flags": flags, "connecting": connecting})
				backward.append({"kind": "connection", "node": node, "signal_name": signal_name, "callback": callback, "flags": flags, "connecting": not connecting})
			state.connections[node][key] = {"connected": connecting, "flags": flags}
		elif kind == "groups":
			if inherited_node(root, node) and not op.get("remove", []).is_empty():
				return error("Remove inherited groups in the base scene")
			if not state.groups.has(node):
				state.groups[node] = {}
				var persistent := EditorInterface.get_node_persistent_groups(node)
				for group in node.get_groups():
					state.groups[node][str(group)] = {"present": true, "persistent": group in persistent}
			for group in op.get("add", []) + op.get("remove", []):
				if str(group).is_empty() or str(group).begins_with("_"):
					return error("Internal/empty groups cannot be edited")
			for group in op.get("add", []):
				var before: Dictionary = state.groups[node].get(str(group), {"present": false, "persistent": false})
				if not before.present or not before.persistent:
					forward.append({"kind": "group", "node": node, "name": group, "add": true, "persistent": true})
					backward.push_front({"kind": "group", "node": node, "name": group, "add": before.present, "persistent": before.persistent})
					state.groups[node][str(group)] = {"present": true, "persistent": true}
			for group in op.get("remove", []):
				var before: Dictionary = state.groups[node].get(str(group), {"present": false, "persistent": false})
				if before.present:
					forward.append({"kind": "group", "node": node, "name": group, "add": false})
					backward.push_front({"kind": "group", "node": node, "name": group, "add": true, "persistent": before.persistent})
					state.groups[node][str(group)] = {"present": false, "persistent": false}

		else:
			return error("Unsupported batch operation: " + kind)
	return {"ok": true, "forward": forward, "backward": backward, "data": result}

func collect_owners(node: Node) -> Array:
	var entries: Array = [[node, node.owner]]
	for child in node.get_children():
		entries.append_array(collect_owners(child))
	return entries

func apply_steps(steps: Array) -> void:
	for step in steps:
		match step.kind:
			"add":
				step.parent.add_child(step.node)
				for entry in step.owners:
					entry[0].owner = entry[1]
			"restore":
				step.parent.add_child(step.node)
				for entry in step.owners:
					entry[0].owner = entry[1]
				if step.index >= 0:
					step.parent.move_child(step.node, mini(step.index, step.parent.get_child_count() - 1))
			"remove": step.parent.remove_child(step.node)
			"property": step.object.set(step.key, step.value)
			"control_state": step.node.call("_edit_set_state", step.value)
			"connection":
				if step.connecting:
					step.node.connect(step.signal_name, step.callback, step.flags)
				else:
					step.node.disconnect(step.signal_name, step.callback)
			"reparent":
				step.node.reparent(step.parent, step.keep_global)
				if step.index >= 0:
					step.parent.move_child(step.node, mini(step.index, step.parent.get_child_count() - 1))
			"group":
				if step.add:
					if step.node.is_in_group(step.name):
						step.node.remove_from_group(step.name)
					step.node.add_to_group(step.name, step.persistent)
				else:
					step.node.remove_from_group(step.name)

func record_steps(undo: EditorUndoRedoManager, steps: Array, inverse: bool) -> void:
	for step in steps:
		if step.kind == "property":
			if inverse:
				undo.add_undo_property(step.object, step.key, step.value)
			else:
				undo.add_do_property(step.object, step.key, step.value)
		elif inverse:
			undo.add_undo_method(self, "apply_steps", [step])
		else:
			undo.add_do_method(self, "apply_steps", [step])
