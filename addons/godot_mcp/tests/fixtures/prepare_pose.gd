extends SceneTree

func _initialize() -> void:
	var root := Node3D.new()
	root.name = "PoseRig"
	root.set_script(load("res://pose_source.gd"))
	var skeleton := Skeleton3D.new()
	skeleton.name = "Rig"
	skeleton.add_bone("Root")
	skeleton.set_bone_rest(0, Transform3D.IDENTITY)
	root.add_child(skeleton)
	skeleton.owner = root
	var player := AnimationPlayer.new()
	player.name = "AnimationPlayer"
	root.add_child(player)
	player.owner = root
	var animation := Animation.new()
	animation.length = 1
	var track := animation.add_track(Animation.TYPE_POSITION_3D)
	animation.track_set_path(track, NodePath("Rig:Root"))
	animation.position_track_insert_key(track, 0, Vector3.ZERO)
	animation.position_track_insert_key(track, 1, Vector3(0, 2, 0))
	track = animation.add_track(Animation.TYPE_METHOD)
	animation.track_set_path(track, NodePath("."))
	animation.track_insert_key(track, 0.25, {"method": "method_track_probe", "args": []})
	var library := AnimationLibrary.new()
	library.add_animation("lift", animation)
	player.add_animation_library("", library)
	var packed := PackedScene.new()
	var status := packed.pack(root)
	if status == OK:
		status = ResourceSaver.save(packed, "res://pose_rig.tscn")
	root.free()
	quit(0 if status == OK else 1)
