@tool
extends RefCounted

static func snapshot_value(value: Variant) -> Variant:
	return value.duplicate(true) if value is Array or value is Dictionary else value

static func editable_property(key: String, info: Dictionary) -> bool:
	return not key in ["script", "owner", "name", "scene_file_path", "resource_path", "resource_scene_unique_id"] and not key.begins_with("_") and not info.is_empty() and bool(int(info.usage) & (PROPERTY_USAGE_STORAGE | PROPERTY_USAGE_EDITOR)) and not bool(int(info.usage) & (PROPERTY_USAGE_READ_ONLY | PROPERTY_USAGE_INTERNAL))

static func validate_value(value: Variant, root: Node) -> String:
	var pending: Array = [value]
	var budget := 4096
	while not pending.is_empty():
		budget -= 1
		if budget < 0:
			return "Property value exceeds the reference validation budget"
		var current: Variant = pending.pop_back()
		if current is Object and current != null:
			if current is Node:
				if root == null or (current != root and (not root.is_ancestor_of(current) or current.owner != root)):
					return "Node values must belong to the target editable scene"
			elif not current is Resource or not EditorInterface.is_scene_class(current.get_class()):
				return "Editor infrastructure objects cannot be stored in project documents"
		elif current is Array:
			if current.size() > budget:
				return "Property array exceeds the reference validation budget"
			pending.append_array(current)
		elif current is Dictionary:
			if current.size() * 2 > budget:
				return "Property dictionary exceeds the reference validation budget"
			pending.append_array(current.keys())
			pending.append_array(current.values())
	return ""
