@tool
extends RefCounted

const LIMIT := 1024
var lease_ms := 120000
var _objects := {}
var _reverse := {}
var _sequence := 0
var _epoch := Crypto.new().generate_random_bytes(12).hex_encode()

func _erase(key: String) -> void:
	if not _objects.has(key):
		return
	var id: int = _objects[key].id
	if _reverse.get(id) == key:
		_reverse.erase(id)
	_objects.erase(key)

func retain_object(object: Object) -> Dictionary:
	if object == null:
		return {}
	var id := object.get_instance_id()
	if _reverse.has(id):
		var previous := str(_reverse[id])
		if resolve(previous) == object:
			return {"_type": "ObjectRef", "ref": previous, "class": object.get_class()}
	if _objects.size() >= LIMIT:
		var now := Time.get_ticks_msec()
		for key in _objects.keys():
			var entry: Dictionary = _objects[key]
			var value: Object = entry.value.get_ref() if entry.value is WeakRef else entry.value
			if now - int(entry.last_used) >= lease_ms or not is_instance_valid(value):
				_erase(key)
	if _objects.size() >= LIMIT:
		return {"_type": "truncated", "reason": "reference_budget"}
	_sequence += 1
	var key := "%s:%d" % [_epoch, _sequence]
	_objects[key] = {"value": object if object is RefCounted else weakref(object), "id": id, "last_used": Time.get_ticks_msec()}
	_reverse[id] = key
	return {"_type": "ObjectRef", "ref": key, "class": object.get_class()}

func resolve(key: String):
	if not _objects.has(key):
		return null
	var entry: Dictionary = _objects[key]
	if Time.get_ticks_msec() - int(entry.last_used) >= lease_ms:
		_erase(key)
		return null
	var value: Object = entry.value.get_ref() if entry.value is WeakRef else entry.value
	if not is_instance_valid(value):
		_erase(key)
		return null
	entry.last_used = Time.get_ticks_msec()
	return value

func release(keys: Array) -> int:
	var released := 0
	for key in keys:
		if _objects.has(str(key)):
			_erase(str(key))
			released += 1
	return released

func encode(value: Variant, depth := 0) -> Variant:
	if depth > 12:
		return {"_type": "truncated", "reason": "depth"}
	match typeof(value):
		TYPE_NIL, TYPE_BOOL, TYPE_STRING:
			return value
		TYPE_INT:
			return {"_type": "int64", "value": str(value)} if value > 9007199254740991 or value < -9007199254740991 else value
		TYPE_FLOAT:
			return value if is_finite(value) else {"_type": "float", "value": str(value)}
		TYPE_STRING_NAME, TYPE_NODE_PATH:
			return {"_type": type_string(typeof(value)), "value": str(value)}
		TYPE_VECTOR2, TYPE_VECTOR2I, TYPE_VECTOR3, TYPE_VECTOR3I, TYPE_VECTOR4, TYPE_VECTOR4I, TYPE_QUATERNION:
			var result := {"_type": type_string(typeof(value)), "x": value.x, "y": value.y}
			if typeof(value) not in [TYPE_VECTOR2, TYPE_VECTOR2I]:
				result["z"] = value.z
			if typeof(value) in [TYPE_VECTOR4, TYPE_VECTOR4I, TYPE_QUATERNION]:
				result["w"] = value.w
			return result
		TYPE_COLOR:
			return {"_type": "Color", "r": value.r, "g": value.g, "b": value.b, "a": value.a}
		TYPE_OBJECT:
			return retain_object(value) if value != null else null
		TYPE_ARRAY:
			var values: Array = []
			for item in value.slice(0, 4096):
				values.append(encode(item, depth + 1))
			return {"_type": "Array", "values": values, "truncated": value.size() > 4096}
		TYPE_DICTIONARY:
			var entries: Array = []
			for key in value:
				if entries.size() >= 4096:
					break
				entries.append([encode(key, depth + 1), encode(value[key], depth + 1)])
			return {"_type": "Dictionary", "entries": entries, "truncated": value.size() > entries.size()}
		_:
			# Godot's text Variant representation handles transforms and packed values
			# without allowing object construction on decode.
			return {"_type": "VariantText", "type": typeof(value), "value": var_to_str(value)}

func decode(value: Variant, depth := 0) -> Dictionary:
	if depth > 12:
		return {"ok": false, "error": "Value depth exceeded"}
	if not value is Dictionary:
		return {"ok": true, "value": value}
	var shape_error := _tag_shape_error(value)
	if not shape_error.is_empty():
		return {"ok": false, "error": shape_error}
	var kind := str(value.get("_type", ""))
	var decoded: Variant
	match kind:
		"ObjectRef":
			decoded = resolve(str(value.get("ref", "")))
			if decoded == null:
				return {"ok": false, "error": "Object reference expired"}
		"Resource":
			var path := str(value.get("path", ""))
			if not path.begins_with("res://") or not ResourceLoader.exists(path):
				return {"ok": false, "error": "Resource path must exist in project"}
			decoded = load(path)
		"int64":
			if not str(value.get("value", "")).is_valid_int():
				return {"ok": false, "error": "Invalid int64"}
			decoded = int(value.value)
			if str(decoded) != str(value.value):
				return {"ok": false, "error": "int64 requires canonical signed 64-bit decimal"}
		"float":
			match str(value.get("value", "")):
				"nan": decoded = NAN
				"inf": decoded = INF
				"-inf": decoded = -INF
				_: return {"ok": false, "error": "Invalid non-finite float tag"}
		"StringName": decoded = StringName(str(value.get("value", "")))
		"NodePath": decoded = NodePath(str(value.get("value", "")))
		"Vector2": decoded = Vector2(value.get("x", 0), value.get("y", 0))
		"Vector2i": decoded = Vector2i(value.get("x", 0), value.get("y", 0))
		"Vector3": decoded = Vector3(value.get("x", 0), value.get("y", 0), value.get("z", 0))
		"Vector3i": decoded = Vector3i(value.get("x", 0), value.get("y", 0), value.get("z", 0))
		"Vector4": decoded = Vector4(value.get("x", 0), value.get("y", 0), value.get("z", 0), value.get("w", 0))
		"Vector4i": decoded = Vector4i(value.get("x", 0), value.get("y", 0), value.get("z", 0), value.get("w", 0))
		"Quaternion": decoded = Quaternion(value.get("x", 0), value.get("y", 0), value.get("z", 0), value.get("w", 1))
		"Color": decoded = Color(value.get("r", 0), value.get("g", 0), value.get("b", 0), value.get("a", 1))
		"VariantText":
			decoded = str_to_var(str(value.get("value", "")))
			if typeof(decoded) != int(value.get("type", -1)) or typeof(decoded) == TYPE_OBJECT:
				return {"ok": false, "error": "Variant type mismatch"}
		"Array":
			decoded = []
			for item in value.get("values", []):
				var parsed := decode(item, depth + 1)
				if not parsed.ok:
					return parsed
				decoded.append(parsed.value)
		"Dictionary":
			decoded = {}
			for pair in value.get("entries", []):
				if not pair is Array or pair.size() != 2:
					return {"ok": false, "error": "Invalid Dictionary entry"}
				var key := decode(pair[0], depth + 1)
				var item := decode(pair[1], depth + 1)
				if not key.ok or not item.ok:
					return {"ok": false, "error": "Invalid Dictionary value"}
				decoded[key.value] = item.value
		_:
			return {"ok": false, "error": "Unsupported value tag: " + kind}
	return {"ok": true, "value": decoded}

func _tag_shape_error(value: Dictionary) -> String:
	if not value.get("_type") is String:
		return "Tagged values require a string _type"
	var kind: String = value._type
	var components: Array = []
	match kind:
		"Vector2", "Vector2i": components = ["x", "y"]
		"Vector3", "Vector3i": components = ["x", "y", "z"]
		"Vector4", "Vector4i", "Quaternion": components = ["x", "y", "z", "w"]
		"Color": components = ["r", "g", "b", "a"]
		"ObjectRef":
			return "ObjectRef requires ref" if not value.get("ref") is String else ""
		"Resource":
			return "Resource requires path" if not value.get("path") is String else ""
		"StringName", "NodePath", "int64", "float":
			return "Value tag requires a string value" if not value.get("value") is String else ""
		"VariantText":
			if not value.get("value") is String or str(value.value).length() > 65536 or not value.get("type") is float and not value.get("type") is int:
				return "VariantText requires a bounded string and numeric type"
			if int(value.type) in [TYPE_OBJECT, TYPE_ARRAY, TYPE_DICTIONARY] or float(value.type) != int(value.type):
				return "Use explicit ObjectRef, Array or Dictionary tags"
			return ""
		"Array":
			return "Array requires values" if not value.get("values") is Array else ""
		"Dictionary":
			return "Dictionary requires entries" if not value.get("entries") is Array else ""
		_:
			return "Unsupported value tag: " + kind
	for key in components:
		var component: Variant = value.get(key)
		if not component is float and not component is int:
			return "Vector and Color components must be numbers"
		if not is_finite(float(component)) or (kind.ends_with("i") and (floor(float(component)) != component or float(component) < -2147483648 or float(component) > 2147483647)):
			return "Invalid component value"
	return ""

func decode_property(value: Variant, info: Dictionary) -> Dictionary:
	var parsed := decode(value)
	if not parsed.ok:
		return parsed
	var expected := int(info.type)
	if expected == TYPE_INT and parsed.value is float:
		if not is_finite(parsed.value) or floor(parsed.value) != parsed.value or abs(parsed.value) > 9007199254740991:
			return {"ok": false, "error": "Integer requires exact numeric value or int64 tag"}
		parsed.value = int(parsed.value)
	elif expected == TYPE_FLOAT and parsed.value is int:
		parsed.value = float(parsed.value)
	if expected != TYPE_NIL and typeof(parsed.value) != expected:
		return {"ok": false, "error": "Property type mismatch"}
	if parsed.value is Object and not str(info.get("class_name", "")).is_empty() and not parsed.value.is_class(str(info.class_name)):
		return {"ok": false, "error": "Object class mismatch"}
	return parsed
