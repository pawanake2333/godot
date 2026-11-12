extends RefCounted

static func invoke(object: Object, args: Dictionary, codec: RefCounted) -> Dictionary:
	if object == null:
		return {"ok": false, "error": {"code": "OBJECT_NOT_FOUND", "message": "Object reference expired"}}
	var method := str(args.method)
	if method in ["free", "queue_free", "set_script", "call", "callv", "call_deferred"] or not object.has_method(method):
		return {"ok": false, "error": {"code": "METHOD_NOT_ALLOWED", "message": method}}
	var descriptor := {}
	for info in object.get_method_list():
		if str(info.name) == method:
			descriptor = info
			break
	var mutating := (int(descriptor.get("flags", 0)) & METHOD_FLAG_CONST) == 0
	if mutating and not bool(args.get("allow_mutation", false)):
		return {"ok": false, "error": {"code": "MUTATION_NOT_DECLARED", "message": "Method is not declared const; set allow_mutation explicitly"}}
	var decoded: Array = []
	for argument in args.get("arguments", []):
		var parsed: Dictionary = codec.decode(argument)
		if not parsed.ok:
			return {"ok": false, "error": {"code": "INVALID_ARGUMENT", "message": parsed.error}}
		decoded.append(parsed.value)
	var parameters: Array = descriptor.get("args", [])
	var defaults: Array = descriptor.get("default_args", [])
	var vararg := (int(descriptor.get("flags", 0)) & METHOD_FLAG_VARARG) != 0
	if decoded.size() < parameters.size() - defaults.size() or (not vararg and decoded.size() > parameters.size()):
		return {"ok": false, "error": {"code": "INVALID_ARGUMENT", "message": "Argument count mismatch"}}
	for index in range(mini(decoded.size(), parameters.size())):
		var parsed: Dictionary = codec.decode_property(args.arguments[index], parameters[index])
		if not parsed.ok:
			return {"ok": false, "error": {"code": "INVALID_ARGUMENT", "message": parsed.error}}
		decoded[index] = parsed.value
	var result: Variant = object.callv(method, decoded)
	return {"ok": true, "result": {"value": codec.encode(result), "mutating": mutating, "undoable": false, "saved": false}}
