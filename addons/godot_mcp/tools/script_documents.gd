@tool
extends RefCounted

var values: RefCounted

func _init(codec: RefCounted) -> void:
	values = codec

func failure(code: String, message: String) -> Dictionary:
	return {"ok": false, "error": {"code": code, "message": message}}

func document_for(path: String) -> ScriptEditorBase:
	for editor in EditorInterface.get_script_editor().get_open_script_editors():
		var resource: Resource = editor.call("get_edited_resource")
		if resource != null and resource.resource_path == path:
			return editor
	return null

func snapshot(editor: ScriptEditorBase) -> Dictionary:
	var text := editor.get_base_editor() as CodeEdit
	var resource: Resource = editor.call("get_edited_resource")
	var path := resource.resource_path
	var disk_hash := FileAccess.get_file_as_string(path).sha256_text() if FileAccess.file_exists(path) else ""
	return {"path": path, "document_id": values.retain_object(text).ref, "source": text.text, "source_kind": "editor", "hash": text.text.sha256_text(), "revision": text.get_version(), "dirty": text.get_version() != text.get_saved_version(), "disk_hash": disk_hash, "differs_from_disk": text.text.sha256_text() != disk_hash}

func invoke(operation: String, args: Dictionary) -> Dictionary:
	var editor: ScriptEditorBase
	if operation == "godot_script_read":
		var path := str(args.path)
		if not path.begins_with("res://") or path.contains(".."):
			return failure("INVALID_PATH", "Select a project script document")
		editor = document_for(path)
		if editor == null and bool(args.get("open_if_needed", false)):
			if not ResourceLoader.exists(path, "Script"):
				return failure("SCRIPT_NOT_FOUND", path)
			var script: Script = load(path)
			EditorInterface.edit_script(script, -1, 0, false)
			editor = document_for(path)
	else:
		var target: Object = values.resolve(str(args.document_id))
		for candidate in EditorInterface.get_script_editor().get_open_script_editors():
			if candidate.get_base_editor() == target:
				editor = candidate
				break
	if editor == null:
		return failure("DOCUMENT_NOT_OPEN", "Read/open an editor document first; expired references cannot patch another document")
	var text := editor.get_base_editor() as CodeEdit
	if text == null:
		return failure("UNSUPPORTED_EDITOR", "This document does not use CodeEdit")
	if text.text.to_utf8_buffer().size() > 1048576:
		return failure("RESULT_TOO_LARGE", "Script document exceeds one MiB")
	if not values.retain_object(text).has("ref"):
		return failure("REFERENCE_BUDGET", "Release references before opening another document handle")
	if operation == "godot_script_read":
		return {"ok": true, "result": snapshot(editor)}
	if str(args.expected_hash) != text.text.sha256_text() or (args.has("expected_revision") and int(args.expected_revision) != text.get_version()):
		return failure("REVISION_CONFLICT", "Editor buffer changed; read the current version before patching")
	if not text.editable:
		return failure("READ_ONLY", "Editor document is read-only")
	var action := str(args.get("action", "apply"))
	if action == "undo":
		if not text.has_undo():
			return failure("HISTORY_EMPTY", "No script edit to undo")
		text.undo()
	elif action == "redo":
		if not text.has_redo():
			return failure("HISTORY_EMPTY", "No script edit to redo")
		text.redo()
	else:
		var source := text.text
		var carets: Array = []
		for index in text.get_caret_count():
			var caret := offset_of(source, text.get_caret_line(index), text.get_caret_column(index))
			var origin := offset_of(source, text.get_selection_origin_line(index), text.get_selection_origin_column(index)) if text.has_selection(index) else caret
			carets.append({"caret": caret, "origin": origin})
		var scroll := Vector2(text.scroll_horizontal, text.scroll_vertical)
		if args.get("edits", []).is_empty():
			return failure("EDITS_REQUIRED", "Apply requires precise source replacements")
		for edit in args.edits:
			if str(edit.before).is_empty() or source.count(str(edit.before)) != 1:
				return failure("AMBIGUOUS_EDIT", "Every match must occur exactly once in the evolving buffer")
			var start := source.find(str(edit.before))
			for caret in carets:
				caret.caret = remap_offset(caret.caret, start, str(edit.before).length(), str(edit.after).length())
				caret.origin = remap_offset(caret.origin, start, str(edit.before).length(), str(edit.after).length())
			source = source.replace(str(edit.before), str(edit.after))
		if source.to_utf8_buffer().size() > 1048576:
			return failure("RESULT_TOO_LARGE", "Patched script would exceed one MiB")
		if source != text.text:
			text.begin_complex_operation()
			text.remove_secondary_carets()
			text.select_all()
			text.insert_text_at_caret(source)
			for index in carets.size():
				var caret := position_of(source, carets[index].caret)
				var origin := position_of(source, carets[index].origin)
				var caret_index := text.add_caret(caret.y, caret.x) if index > 0 else 0
				if caret_index >= 0:
					text.select(origin.y, origin.x, caret.y, caret.x, caret_index)
			text.merge_overlapping_carets()
			text.scroll_horizontal = int(scroll.x)
			text.scroll_vertical = scroll.y
			text.end_complex_operation()
	return {"ok": true, "result": {"applied": true, "saved": false, "undoable": true, "document": snapshot(editor), "validation": "editor_deferred"}}

static func offset_of(source: String, line: int, column: int) -> int:
	var lines := source.split("\n")
	var offset := 0
	for index in mini(line, lines.size()):
		offset += lines[index].length() + 1
	return mini(source.length(), offset + column)

static func position_of(source: String, offset: int) -> Vector2i:
	var prefix := source.left(offset)
	return Vector2i(prefix.length() - prefix.rfind("\n") - 1, prefix.count("\n"))

static func remap_offset(offset: int, start: int, before: int, after: int) -> int:
	if offset <= start:
		return offset
	if offset >= start + before:
		return offset + after - before
	return start + mini(offset - start, after)
