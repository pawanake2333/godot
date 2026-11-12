extends RefCounted

static func capture(host: Node, codec: RefCounted, args: Dictionary) -> Dictionary:
	if DisplayServer.get_name() == "headless":
		return {"ok": false, "error": {"code": "UNSUPPORTED_DISPLAY", "message": "Dummy renderer cannot capture GPU pixels"}}
	var target := str(args.get("target", "game"))
	var viewport: Viewport = host.get_tree().root
	if target != "game":
		var object: Object = codec.resolve(str(args.object_ref)) if args.has("object_ref") else host.get_tree().root.get_node_or_null(NodePath(str(args.get("node_path", ""))))
		if target == "camera":
			if not (object is Camera2D or object is Camera3D):
				return {"ok": false, "error": {"code": "INVALID_CAMERA", "message": "Select a Camera2D or Camera3D"}}
			viewport = object.get_viewport()
			if (object is Camera3D and viewport.get_camera_3d() != object) or (object is Camera2D and viewport.get_camera_2d() != object):
				return {"ok": false, "error": {"code": "CAMERA_NOT_CURRENT", "message": "Capture requires the current camera of its explicit viewport"}}
		elif object is Viewport:
			viewport = object
		else:
			return {"ok": false, "error": {"code": "INVALID_VIEWPORT", "message": "Select an explicit Viewport"}}
	if viewport.get_visible_rect().size.x * viewport.get_visible_rect().size.y > 4194304:
		return {"ok": false, "error": {"code": "RESULT_TOO_LARGE", "message": "Capture source exceeds four million pixels"}}
	# Force one offscreen draw; preserve project viewport scheduling.
	var previous: int = viewport.render_target_update_mode if viewport is SubViewport else -1
	RenderingServer.viewport_set_update_mode(viewport.get_viewport_rid(), RenderingServer.VIEWPORT_UPDATE_ALWAYS)
	RenderingServer.force_draw(false)
	var image := viewport.get_texture().get_image()
	if viewport is SubViewport:
		viewport.render_target_update_mode = previous
	if image == null or image.is_empty():
		return {"ok": false, "error": {"code": "CAPTURE_UNAVAILABLE", "message": "Viewport produced no image"}}
	if args.has("width"):
		image.resize(int(args.width), int(args.get("height", args.width)), Image.INTERPOLATE_BILINEAR)
	var png := image.save_png_to_buffer()
	if png.size() > 3145728:
		return {"ok": false, "error": {"code": "RESULT_TOO_LARGE", "message": "PNG exceeds three MiB; request a smaller image"}}
	var center := image.get_pixel(image.get_width() / 2, image.get_height() / 2)
	return {"ok": true, "value": {"png_base64": Marshalls.raw_to_base64(png), "width": image.get_width(), "height": image.get_height(), "driver": RenderingServer.get_current_rendering_driver_name(), "renderer": RenderingServer.get_current_rendering_method(), "process_frame": Engine.get_process_frames(), "frames_drawn": Engine.get_frames_drawn(), "target": target, "viewport_path": str(viewport.get_path()), "center_rgba": [center.r, center.g, center.b, center.a], "window_visible": DisplayServer.window_can_draw()}}
