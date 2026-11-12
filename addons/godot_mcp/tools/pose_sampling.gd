@tool
extends RefCounted

var codec: RefCounted

func _init(values: RefCounted) -> void:
	codec = values

func fail(message: String) -> Dictionary:
	return {"ok": false, "error": {"code": "INVALID_POSE_SAMPLE", "message": message}}

func subtree(root: Node) -> Array[Node]:
	var nodes: Array[Node] = [root]
	var index := 0
	while index < nodes.size():
		var node := nodes[index]
		index += 1
		if nodes.size() + node.get_child_count() > 4096:
			return []
		for child in node.get_children():
			nodes.append(child)
	return nodes

func sample(object: Object, args: Dictionary) -> Dictionary:
	if not object is AnimationMixer:
		return fail("Pose sampling requires an AnimationPlayer or AnimationTree")
	var root: Node = object.get_node_or_null(object.root_node)
	if root == null:
		return fail("Animation root does not exist")
	var mode := str(args.mode)
	if mode == "live":
		var captured := capture(root, args)
		if not captured.ok:
			return captured
		return {"ok": true, "result": {"mode": mode, "sampling": "applied_pose", "samples": [captured.result], "truncated": captured.truncated, "sampling_stage": "current_scene_state", "side_effects": [], "process_frame": Engine.get_process_frames(), "physics_frame": Engine.get_physics_frames(), "playback_modified": false}}
	if not object is AnimationPlayer or not object.has_animation(str(args.animation_name)):
		return fail("Isolated sampling requires a named AnimationPlayer animation")
	if subtree(root).is_empty():
		return fail("Animation root exceeds 4096 nodes; select a smaller animation root")
	# Zero flags omit scripts, signal connections, groups and scene instantiation.
	# Only native objects and document property values are copied.
	var copy := root.duplicate(0)
	if copy == null:
		return fail("Could not duplicate the animation root")
	var player := copy.get_node_or_null(root.get_path_to(object)) as AnimationPlayer
	if player == null:
		copy.free()
		return fail("AnimationPlayer is outside its configured root")
	for node in subtree(copy):
		node.process_mode = Node.PROCESS_MODE_DISABLED
		if node is AnimationMixer:
			node.active = false
			node.callback_mode_process = AnimationMixer.ANIMATION_CALLBACK_MODE_PROCESS_MANUAL
		if node is AnimationPlayer:
			node.autoplay = ""
		if node is Skeleton3D:
			node.modifier_callback_mode_process = Skeleton3D.MODIFIER_CALLBACK_MODE_PROCESS_MANUAL
		if node is SkeletonModifier3D:
			node.active = false
		if node is AudioStreamPlayer or node is AudioStreamPlayer2D or node is AudioStreamPlayer3D:
			node.autoplay = false
			node.stream = null
	# Animation libraries are copied before filtering unsafe/shared-resource tracks.
	var ignored: Array = []
	for library_name in player.get_animation_library_list():
		var library := player.get_animation_library(library_name).duplicate(true) as AnimationLibrary
		player.remove_animation_library(library_name)
		player.add_animation_library(library_name, library)
		for animation_name in library.get_animation_list():
			var animation := library.get_animation(animation_name)
			for track in range(animation.get_track_count()):
				var path := animation.track_get_path(track)
				var target: Node = copy.get_node_or_null(NodePath(path.get_concatenated_names())) if path.get_name_count() > 0 else copy
				var safe := target != null and (target == copy or copy.is_ancestor_of(target)) and animation.track_get_type(track) not in [Animation.TYPE_METHOD, Animation.TYPE_AUDIO, Animation.TYPE_ANIMATION]
				if safe and path.get_subname_count() > 0 and animation.track_get_type(track) in [Animation.TYPE_VALUE, Animation.TYPE_BEZIER]:
					var found := false
					for info in target.get_property_list():
						if str(info.name) == str(path.get_subname(0)):
							found = true
							if int(info.type) == TYPE_OBJECT:
								safe = false
					safe = safe and found
				if not safe:
					animation.track_set_enabled(track, false)
					ignored.append({"animation": animation_name, "track": track, "path": str(path), "reason": "event_or_external_resource_target"})
	var viewport := SubViewport.new()
	viewport.size = Vector2i(1, 1)
	viewport.render_target_update_mode = SubViewport.UPDATE_DISABLED
	viewport.world_2d = World2D.new()
	viewport.own_world_3d = true
	viewport.process_mode = Node.PROCESS_MODE_DISABLED
	viewport.add_child(copy)
	(Engine.get_main_loop() as SceneTree).root.add_child(viewport)
	player.active = true
	var samples: Array = []
	var truncated := false
	var failure := {}
	for time in args.times:
		player.play(str(args.animation_name), 0)
		# update_only suppresses method, audio and nested playback tracks.
		player.seek(float(time), true, true)
		var captured := capture(copy, args)
		if not captured.ok:
			failure = captured
			break
		captured.result["time"] = time
		samples.append(captured.result)
		truncated = truncated or captured.truncated
	viewport.free()
	if not failure.is_empty():
		return failure
	return {"ok": true, "result": {"mode": mode, "sampling": "applied_pose", "samples": samples, "truncated": truncated, "sampling_stage": "after_single_clip_seek_before_modifiers", "side_effects": [], "playback_modified": false, "project_scripts_copied": false, "world_isolated": true, "ignored_tracks": ignored}}

func capture(root: Node, args: Dictionary) -> Dictionary:
	var nodes := subtree(root)
	if nodes.is_empty():
		return fail("Pose query exceeds 4096 scene nodes")
	var selected: Array[Skeleton3D] = []
	if args.has("skeleton_paths"):
		for path in args.skeleton_paths:
			var node := root.get_node_or_null(NodePath(path))
			if not node is Skeleton3D or (node != root and not root.is_ancestor_of(node)):
				return fail("Skeleton target must belong to the animation root")
			selected.append(node)
	else:
		for node in nodes:
			if node is Skeleton3D:
				selected.append(node)
	var skeletons: Array = []
	var remaining := int(args.get("max_bones", 128))
	var truncated := false
	for skeleton in selected:
		var bones: Array = []
		for index in range(skeleton.get_bone_count()):
			if remaining <= 0:
				truncated = true
				break
			bones.append({"index": index, "name": skeleton.get_bone_name(index), "parent": skeleton.get_bone_parent(index), "position": codec.encode(skeleton.get_bone_pose_position(index)), "rotation": codec.encode(skeleton.get_bone_pose_rotation(index)), "scale": codec.encode(skeleton.get_bone_pose_scale(index)), "local_pose": codec.encode(skeleton.get_bone_pose(index)), "global_pose": codec.encode(skeleton.get_bone_global_pose(index))})
			remaining -= 1
		skeletons.append({"path": str(root.get_path_to(skeleton)), "bones": bones, "coordinate_space": "skeleton"})
	var properties: Array = []
	for selector in args.get("properties", []):
		var node := root.get_node_or_null(NodePath(selector.node_path))
		if node == null or (node != root and not root.is_ancestor_of(node)):
			return fail("Property target must belong to the animation root")
		var property := NodePath(":" + str(selector.property).trim_prefix(":"))
		if property.get_subname_count() != 1:
			return fail("Pose property selectors require a direct node property; read vector components from the typed result")
		var found := false
		for info in node.get_property_list():
			if str(info.name) == str(property.get_subname(0)):
				found = true
		if not found:
			return fail("Selected node property does not exist")
		properties.append({"node_path": selector.node_path, "property": selector.property, "value": codec.encode(node.get_indexed(property))})
	return {"ok": true, "result": {"skeletons": skeletons, "properties": properties}, "truncated": truncated}
