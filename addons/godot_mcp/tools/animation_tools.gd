@tool
extends RefCounted

var codec: RefCounted
const PoseSampling = preload("res://addons/godot_mcp/tools/pose_sampling.gd")

func _init(values: RefCounted) -> void:
	codec = values

func fail(message: String) -> Dictionary:
	return {"ok": false, "error": {"code": "INVALID_ANIMATION", "message": message}}

func resolve_animation(object: Object, name: String) -> Animation:
	if object is Animation:
		return object
	if object is AnimationPlayer and object.has_animation(name):
		return object.get_animation(name)
	if object is AnimationLibrary and object.has_animation(name):
		return object.get_animation(name)
	return null

func read(operation: String, object: Object, args: Dictionary) -> Dictionary:
	if operation == "godot_animation_sample" and args.get("mode", "tracks") != "tracks":
		return PoseSampling.new(codec).sample(object, args)
	if object == null:
		return fail("Animation object is absent or expired")
	if operation == "godot_animation_list":
		var names: PackedStringArray
		if object is AnimationPlayer or object is AnimationLibrary:
			names = object.get_animation_list()
		elif object is Animation:
			names = PackedStringArray([""])
		else:
			return fail("Expected AnimationPlayer, AnimationLibrary or Animation")
		var result: Array = []
		for name in names.slice(0, 256):
			var animation := resolve_animation(object, name)
			result.append({"name": name, "length": animation.length, "loop_mode": animation.loop_mode, "tracks": animation.get_track_count(), "animation": codec.retain_object(animation)})
		return {"ok": true, "result": {"animations": result, "truncated": names.size() > 256}}
	var animation := resolve_animation(object, str(args.get("animation_name", "")))
	if animation == null:
		return fail("Animation not found")
	var tracks: Array = []
	var remaining := int(args.get("max_keys", 1024))
	for track in range(mini(animation.get_track_count(), 256)):
		var entry := {"index": track, "type": animation.track_get_type(track), "path": str(animation.track_get_path(track)), "enabled": animation.track_is_enabled(track), "interpolation": animation.track_get_interpolation_type(track)}
		if operation == "godot_animation_sample":
			var samples: Array = []
			for time in args.times:
				if remaining <= 0:
					break
				var sample: Variant
				match animation.track_get_type(track):
					Animation.TYPE_VALUE: sample = animation.value_track_interpolate(track, time)
					Animation.TYPE_POSITION_3D: sample = animation.position_track_interpolate(track, time)
					Animation.TYPE_ROTATION_3D: sample = animation.rotation_track_interpolate(track, time)
					Animation.TYPE_SCALE_3D: sample = animation.scale_track_interpolate(track, time)
					Animation.TYPE_BLEND_SHAPE: sample = animation.blend_shape_track_interpolate(track, time)
					Animation.TYPE_BEZIER: sample = animation.bezier_track_interpolate(track, time)
					_:
						entry["unsupported_sampling"] = true
						break
				samples.append({"time": time, "value": codec.encode(sample)})
				remaining -= 1
			entry["samples"] = samples
		else:
			var keys: Array = []
			for key in range(animation.track_get_key_count(track)):
				if remaining <= 0:
					break
				keys.append({"time": animation.track_get_key_time(track, key), "value": codec.encode(animation.track_get_key_value(track, key)), "transition": animation.track_get_key_transition(track, key)})
				remaining -= 1
			entry["keys"] = keys
			entry["key_count"] = animation.track_get_key_count(track)
		tracks.append(entry)
	return {"ok": true, "result": {"length": animation.length, "loop_mode": animation.loop_mode, "tracks": tracks, "truncated": remaining <= 0 or animation.get_track_count() > 256, "sampling": "track_values" if operation == "godot_animation_sample" else null}}

func edited_copy(animation: Animation, args: Dictionary) -> Dictionary:
	var copy := animation.duplicate() as Animation
	var operation := str(args.operation)
	var track := int(args.get("track", -1))
	if operation not in ["add_track", "set_metadata"] and (track < 0 or track >= copy.get_track_count()):
		return fail("Track index out of range")
	match operation:
		"add_track":
			track = copy.add_track(int(args.get("track_type", Animation.TYPE_VALUE)) as Animation.TrackType)
			copy.track_set_path(track, NodePath(str(args.get("path", ""))))
		"remove_track": copy.remove_track(track)
		"set_key":
			var decoded: Dictionary = codec.decode(args.value)
			if not decoded.ok:
				return fail(decoded.error)
			if copy.track_insert_key(track, float(args.time), decoded.value) < 0:
				return fail("Key value does not match track type")
		"remove_key":
			var key := int(args.get("key", -1))
			if key < 0 or key >= copy.track_get_key_count(track):
				return fail("Key index out of range")
			copy.track_remove_key(track, key)
		"set_metadata":
			if args.has("length"):
				copy.length = float(args.length)
			if args.has("loop_mode"):
				copy.loop_mode = int(args.loop_mode) as Animation.LoopMode
		_:
			return fail("Unsupported animation operation")
	return {"ok": true, "copy": copy, "track": track}

func restore(animation: Animation, snapshot: Animation) -> void:
	while animation.get_track_count() > 0:
		animation.remove_track(animation.get_track_count() - 1)
	for track in range(snapshot.get_track_count()):
		snapshot.copy_track(track, animation)
	animation.length = snapshot.length
	animation.loop_mode = snapshot.loop_mode
	animation.step = snapshot.step
