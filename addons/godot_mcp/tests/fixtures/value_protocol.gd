extends SceneTree

const Values = preload("res://addons/godot_mcp/contract/values.gd")

func _initialize() -> void:
	var codec := Values.new()
	for value in [9223372036854775807, -9223372036854775807, Vector2(1, 2), Vector3i(3, 4, 5), Quaternion(0, 0, 0, 1), Color(1, 0.5, 0, 1), Transform3D.IDENTITY, PackedVector3Array([Vector3.ONE]), {Vector2.ONE: [42, "value"]}]:
		var decoded := codec.decode(codec.encode(value))
		if not decoded.ok or decoded.value != value:
			push_error("Variant roundtrip failed: " + str(value))
			quit(1)
			return
	for value in [INF, -INF, NAN]:
		var decoded := codec.decode(codec.encode(value))
		if not decoded.ok or (not is_nan(value) and decoded.value != value) or (is_nan(value) and not is_nan(decoded.value)):
			quit(5)
			return
	if codec.decode({"_type": "int64", "value": "9223372036854775808"}).ok:
		quit(6)
		return
	if codec.decode({"_type": "UnsupportedTag"}).ok:
		quit(2)
		return
	for malformed in [{"_type": "Vector3", "x": "wrong", "y": 0, "z": 0}, {"_type": "Array", "values": 1}, {"_type": "Color", "r": 0}, {"_type": "VariantText", "type": TYPE_ARRAY, "value": "[]"}]:
		if codec.decode(malformed).ok:
			quit(10)
			return
	var node := Node.new()
	var reference: Dictionary = codec.retain_object(node)
	node.free()
	if codec.resolve(reference.ref) != null:
		quit(3)
		return
	var resource := Theme.new()
	var retained: Dictionary = codec.retain_object(resource)
	codec.release([retained.ref])
	if codec.resolve(retained.ref) != null:
		quit(4)
		return
	var leased := codec.retain_object(resource)
	codec.lease_ms = 0
	if codec.resolve(leased.ref) != null:
		quit(7)
		return
	codec.lease_ms = 120000
	var renewed := codec.retain_object(resource)
	if renewed.ref == leased.ref or codec.resolve(renewed.ref) != resource:
		quit(8)
		return
	if codec.release([renewed.ref, renewed.ref]) != 1:
		quit(9)
		return
	print("MCP_VARIANT_PROTOCOL_OK")
	quit(0)
