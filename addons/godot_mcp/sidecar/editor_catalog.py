"""Typed schemas for the editor document and resource tools."""
from mcp_services import tool, STRING

STRINGS = {"type": "array", "items": STRING}
TARGET = {"scene_id": STRING, "node_path": {"type": "string"}, "object_ref": STRING}
EDIT = {**TARGET, "expected_revision": {"type": "integer", "minimum": 0}}
EDITOR_TOOLS = [
    tool("godot_output_control", "Get, configure, read or clear the real Output dock. Panel clearing preserves diagnostics; diagnostic clearing requires an explicit cutoff cursor and editor epoch.", {
        "action": {"type": "string", "enum": ["get", "configure", "read", "clear"]},
        "visible": {"type": "boolean"}, "collapse": {"type": "boolean"},
        "search": {"type": "string", "maxLength": 1024},
        "levels": {"type": "array", "maxItems": 4, "items": {"type": "string", "enum": ["info", "error", "warning", "editor"]}},
        "scope": {"type": "string", "enum": ["panel", "diagnostics", "all"]},
        "editor_epoch": STRING, "through_cursor": {"type": "integer", "minimum": 0},
        "offset": {"type": "integer", "minimum": 0, "maximum": 1048576},
        "max_chars": {"type": "integer", "minimum": 1, "maximum": 65536},
        "expected_revision": {"type": "integer", "minimum": 0},
    }),
    tool("godot_scene_list", "List scene documents and monotonic editor-history revision, invalidated by any scene/global Undo or Redo."),
    tool("godot_class_describe", "Describe ClassDB properties, methods and signals.", {"class_name": STRING}, ["class_name"]),
    tool("godot_resource_load", "Load a project resource and retain a bounded reference.", {"path": STRING}, ["path"]),
    tool("godot_resource_create", "Create an unsaved Resource of a native instantiable class.", {"class_name": STRING}, ["class_name"]),
    tool("godot_resource_duplicate", "Duplicate a retained resource, optionally including subresources.", {"object_ref": STRING, "deep": {"type": "boolean"}}, ["object_ref"]),
    tool("godot_resource_save", "Save a project Resource through native editor bookkeeping; never replace another cached resource or bypass a scene/script/shader document.", {"object_ref": STRING, "path": STRING, "overwrite": {"type": "boolean"}}, ["object_ref", "path"]),
    tool("godot_object_release", "Release retained automation object references without destroying Nodes.", {"object_refs": STRINGS}, ["object_refs"]),
    tool("godot_object_inspect", "Inspect typed editor object properties with reusable references.", {**TARGET, "properties": STRINGS, "max_properties": {"type": "integer", "minimum": 1, "maximum": 256}}, ["scene_id"]),
    tool("godot_object_set_properties", "Edit typed properties through the native Inspector command, shared UI completion and scene/resource Undo history.", {**EDIT, "changes": {"type": "object"}}, ["scene_id", "expected_revision", "changes"]),
    tool("godot_project_settings_get", "Read typed project settings.", {"keys": STRINGS}, ["keys"]),
]

EDITOR_TOOLS.extend([
    tool("godot_scene_create", "Create an unsaved scene document with a native root.", {"root_type": STRING, "root_name": STRING}, ["root_type", "root_name"]),
    tool("godot_scene_open", "Open a project scene document.", {"path": STRING}, ["path"]),
    tool("godot_scene_save", "Save the active identified document and verify its written path.", {"scene_id": STRING, "expected_revision": {"type": "integer"}, "path": STRING, "overwrite": {"type": "boolean"}}, ["scene_id", "expected_revision"]),
    tool("godot_scene_close", "Close an identified active scene with explicit dirty policy.", {"scene_id": STRING, "expected_revision": {"type": "integer"}, "unsaved": {"type": "string", "enum": ["reject", "save", "discard"]}}, ["scene_id", "expected_revision"]),
    tool("godot_scene_undo", "Undo the identified active scene history.", {"scene_id": STRING, "expected_revision": {"type": "integer"}}, ["scene_id", "expected_revision"]),
    tool("godot_scene_redo", "Redo the identified active scene history.", {"scene_id": STRING, "expected_revision": {"type": "integer"}}, ["scene_id", "expected_revision"]),
])

OPERATIONS = {"type": "array", "minItems": 1, "maxItems": 256, "items": {"type": "object", "properties": {
    "op": {"type": "string", "enum": ["create", "instance_scene", "duplicate", "delete", "rename", "reparent", "groups", "set", "attach", "connect", "disconnect"]},
    "node_path": {"type": "string"}, "parent_path": {"type": "string"}, "name": STRING,
    "type": STRING, "path": STRING, "index": {"type": "integer", "minimum": -1},
    "keep_global_transform": {"type": "boolean"}, "add": STRINGS, "remove": STRINGS,
    "id": STRING, "changes": {"type": "object"}, "script_path": {}, "signal_name": STRING,
    "receiver_path": {"type": "string"}, "method": STRING, "binds": {"type": "array", "maxItems": 32, "items": {}}, "unbind": {"type": "integer", "minimum": 0, "maximum": 32}, "flags": {"type": "integer", "enum": [0, 1, 4, 5]},
}, "required": ["op"], "additionalProperties": False}}
EDITOR_TOOLS.extend([
    tool("godot_edit_batch", "Prevalidate structural scene edits, then apply one undo action.", {"scene_id": STRING, "expected_revision": {"type": "integer", "minimum": 0}, "operations": OPERATIONS, "dry_run": {"type": "boolean"}}, ["scene_id", "expected_revision", "operations"]),
])

EDITOR_TOOLS.extend([
    tool("godot_filesystem_scan", "Wait for the editor filesystem scan completion event."),
])

EDITOR_TOOLS.extend([
    tool("godot_script_reload", "Reload closed project scripts; running objects are not rebuilt.", {"paths": {"type": "array", "minItems": 1, "maxItems": 256, "items": STRING}}, ["paths"]),
    tool("godot_scene_tree", "Read a bounded scene-relative tree snapshot with references and owner metadata.", {"scene_id": STRING, "max_nodes": {"type": "integer", "minimum": 1, "maximum": 4096}}, ["scene_id"]),
    tool("godot_object_query", "Find scene objects by native class and name pattern.", {"scene_id": STRING, "max_nodes": {"type": "integer", "minimum": 1, "maximum": 4096}, "class_name": STRING, "name_pattern": STRING}, ["scene_id"]),
])

for entry in EDITOR_TOOLS:
    if entry["name"] in {"godot_object_inspect", "godot_object_set_properties", "godot_object_release", "godot_scene_tree", "godot_object_query"}:
        entry["inputSchema"]["properties"].update({"context": {"type": "string", "enum": ["editor", "runtime"]}, "session_id": STRING})
        entry["inputSchema"]["required"] = [key for key in entry["inputSchema"]["required"] if key not in {"scene_id", "expected_revision"}]
INPUT_PROPERTIES = {
    "session_id": STRING,
    "kind": {"type": "string", "enum": ["key", "action_state", "action_event", "mouse_button", "mouse_motion"]},
    "pressed": {"type": "boolean"}, "echo": {"type": "boolean"}, "keycode": {"type": "integer", "minimum": 0},
    "physical_keycode": {"type": "integer", "minimum": 0}, "unicode": {"type": "integer", "minimum": 0, "maximum": 1114111},
    "action": STRING, "strength": {"type": "number", "minimum": 0, "maximum": 1},
    "button": {"type": "integer", "enum": [1, 2, 3, 8, 9]},
    **{key: {"type": "number"} for key in ["x", "y", "delta_x", "delta_y"]},
    **{key: {"type": "boolean"} for key in ["shift", "ctrl", "alt", "meta"]},
}
EDITOR_TOOLS.extend([
    tool("godot_run_list", "List ready debug sessions with stable runtime-generated identities."),
    tool("godot_input_send", "Send typed viewport key/action/mouse input to an explicit session.", INPUT_PROPERTIES, ["session_id", "kind"]),
    tool("godot_input_release_all", "Release inputs held by this MCP connection in the selected session.", {"session_id": STRING}, ["session_id"]),
    tool("godot_runtime_wait", "Wait bounded frame, property or signal conditions using engine notifications.", {"condition": {"type": "string", "enum": ["frames", "property", "signal"]}, "object_ref": STRING, "node_path": STRING, "property": STRING, "comparison": {"type": "string", "enum": ["eq", "ne", "lt", "le", "gt", "ge"]}, "value": {}, "signal_name": STRING, "session_id": STRING, "frames": {"type": "integer", "minimum": 1, "maximum": 120}, "clock": {"type": "string", "enum": ["process", "physics"]}, "timeout_ms": {"type": "integer", "minimum": 1, "maximum": 9000}}, ["session_id"]),
])

EDITOR_TOOLS.append(tool("godot_run_wait", "Wait for runtime ready handshakes using debugger session notifications.", {"minimum_sessions": {"type": "integer", "minimum": 1, "maximum": 16}, "timeout_ms": {"type": "integer", "minimum": 1, "maximum": 9000}}))

INPUT_PROPERTIES["kind"]["enum"].extend(["text", "wheel"])
INPUT_PROPERTIES.update({"text": {"type": "string", "maxLength": 4096}, "direction": {"type": "string", "enum": ["up", "down", "left", "right"]}, "factor": {"type": "number", "minimum": 0.01, "maximum": 100}})
SEQUENCE_EVENT = {"type": "object", "properties": {k: v for k, v in INPUT_PROPERTIES.items() if k != "session_id"}, "required": ["kind"], "additionalProperties": False}
EDITOR_TOOLS.append(tool("godot_input_sequence", "Run frame-relative input events and release held input on completion or interruption.", {"session_id": STRING, "timeout_ms": {"type": "integer", "minimum": 1, "maximum": 9000}, "steps": {"type": "array", "minItems": 1, "maxItems": 256, "items": {"type": "object", "properties": {"after_frames": {"type": "integer", "minimum": 0, "maximum": 120}, "event": SEQUENCE_EVENT}, "required": ["event"], "additionalProperties": False}}}, ["session_id", "steps"]))

EDITOR_TOOLS.extend([
    tool("godot_ui_focus", "Focus an engine Control in an explicit runtime session; no OS focus changes.", {"session_id": STRING, "node_path": STRING}, ["session_id", "node_path"]),
    tool("godot_ui_inspect", "Inspect bounded runtime Control geometry, visibility, focus and text.", {"session_id": STRING, "node_path": STRING, "max_nodes": {"type": "integer", "minimum": 1, "maximum": 4096}}, ["session_id", "node_path"]),
])

INPUT_PROPERTIES["kind"]["enum"].extend(["touch", "touch_motion", "joy_button", "joy_motion"])
INPUT_PROPERTIES.update({"index": {"type": "integer", "minimum": 0, "maximum": 31}, "device": {"type": "integer", "minimum": 0, "maximum": 15}, "joy_button": {"type": "integer", "minimum": 0, "maximum": 127}, "axis": {"type": "integer", "minimum": 0, "maximum": 9}, "value": {"type": "number", "minimum": -1, "maximum": 1}})
SEQUENCE_EVENT["properties"].update({k: v for k, v in INPUT_PROPERTIES.items() if k != "session_id"})

ANIMATION_TARGET = {**TARGET, "context": {"type": "string", "enum": ["editor", "runtime"]}, "session_id": STRING, "animation_name": {"type": "string"}, "max_keys": {"type": "integer", "minimum": 1, "maximum": 4096}}
EDITOR_TOOLS.extend([
    tool("godot_animation_list", "List animations in an AnimationPlayer, library or animation resource.", ANIMATION_TARGET),
    tool("godot_animation_inspect", "Read bounded animation track paths, key times and typed key values.", ANIMATION_TARGET),
    tool("godot_animation_sample", "Interpolate animation track values at requested times without modifying playback.", {**ANIMATION_TARGET, "times": {"type": "array", "minItems": 1, "maxItems": 256, "items": {"type": "number", "minimum": 0}}}, ["times"]),
    tool("godot_animation_edit", "Edit animation tracks or keys using native scene/resource history rules.", {**EDIT, "animation_name": {"type": "string"}, "operation": {"type": "string", "enum": ["add_track", "remove_track", "set_key", "remove_key", "set_metadata"]}, "track": {"type": "integer", "minimum": 0}, "track_type": {"type": "integer", "minimum": 0, "maximum": 8}, "path": {"type": "string"}, "time": {"type": "number", "minimum": 0}, "value": {}, "key": {"type": "integer", "minimum": 0}, "length": {"type": "number", "minimum": 0.001}, "loop_mode": {"type": "integer", "enum": [0, 1, 2]}}, ["scene_id", "expected_revision", "operation"]),
    tool("godot_object_call", "Editor calls are const-only, except two undo-backed animation-library add methods. Runtime non-const calls require allow_mutation.", {**ANIMATION_TARGET, "method": STRING, "arguments": {"type": "array", "maxItems": 64, "items": {}}, "allow_mutation": {"type": "boolean"}, "expected_revision": {"type": "integer", "minimum": 0}}, ["method"]),
])

EDITOR_TOOLS.extend([
    tool("godot_project_settings_set", "Apply version-checked project settings in native global Undo history; saving also requires the expected disk hash.", {"changes": {"type": "object"}, "save": {"type": "boolean"}, "expected_revision": STRING, "expected_disk_hash": STRING}, ["changes", "expected_revision"]),
    tool("godot_editor_select", "Select an editor panel or scene nodes using editor APIs.", {"panel": {"type": "string", "enum": ["2d", "3d", "script", "game"]}, "node_paths": STRINGS, "scene_id": STRING}),
])

EDITOR_TOOLS = [entry for entry in EDITOR_TOOLS if entry["name"] not in {"godot_run_list", "godot_run_wait"}]

EDITOR_TOOLS.extend([
    tool("godot_debugger_breakpoint_set", "Register a project script breakpoint for current and future editor sessions.", {"path": STRING, "line": {"type": "integer", "minimum": 1}}, ["path", "line"]),
    tool("godot_debugger_breakpoint_clear", "Clear the precise script path and one-based breakpoint line.", {"path": STRING, "line": {"type": "integer", "minimum": 1}}, ["path", "line"]),
    tool("godot_debugger_state", "Read paused state, bounded stack and optional frame variables without runtime frame execution.", {"wait_paused": {"type": "boolean"}, "session_id": STRING, "frame": {"type": "integer", "minimum": 0, "maximum": 255}, "max_variables": {"type": "integer", "minimum": 1, "maximum": 2048}, "timeout_ms": {"type": "integer", "minimum": 1, "maximum": 9000}}, ["session_id"]),
    tool("godot_debugger_control", "Pause, resume or step one precise debugger session, waiting for the observed state transition.", {"session_id": STRING, "action": {"type": "string", "enum": ["pause", "continue", "step_into", "step_over", "step_out"]}, "timeout_ms": {"type": "integer", "minimum": 1, "maximum": 9000}}, ["session_id", "action"]),
])

EDITOR_TOOLS.extend([
    tool("godot_monitors_read", "Read selected engine Performance counters with units and renderer availability.", {"session_id": STRING, "metrics": {"type": "array", "minItems": 1, "maxItems": 64, "items": STRING}}, ["session_id"]),
    tool("godot_profiler_capture", "Capture bounded frames from the existing script/server or visual engine profiler.", {"session_id": STRING, "kind": {"type": "string", "enum": ["servers", "visual"]}, "frames": {"type": "integer", "minimum": 1, "maximum": 120}, "timeout_ms": {"type": "integer", "minimum": 1, "maximum": 9000}}, ["session_id"]),
])

EDITOR_TOOLS.extend([
    tool("godot_script_read", "Read an editor CodeEdit buffer, its document identity, unsaved revision and disk difference; no disk-only fallback.", {"path": STRING, "open_if_needed": {"type": "boolean"}}, ["path"]),
    tool("godot_script_patch", "Patch or undo/redo an identified editor script buffer; never writes a disk file.", {"document_id": STRING, "expected_hash": STRING, "expected_revision": {"type": "integer", "minimum": 0}, "action": {"type": "string", "enum": ["apply", "undo", "redo"]}, "edits": {"type": "array", "minItems": 1, "maxItems": 256, "items": {"type": "object", "properties": {"before": STRING, "after": {"type": "string"}}, "required": ["before", "after"], "additionalProperties": False}}}, ["document_id", "expected_hash"]),
])

for entry in EDITOR_TOOLS:
    if entry["name"] in {"godot_scene_tree", "godot_object_query"}:
        entry["inputSchema"]["properties"].pop("max_nodes", None)
        entry["inputSchema"]["properties"].update({"root_path": STRING, "snapshot": STRING, "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 1024}})

for entry in EDITOR_TOOLS:
    if entry["name"] == "godot_animation_sample":
        entry["description"] = "Read track values, sample an isolated script-free clip pose, or inspect the current applied live pose without changing playback."
        entry["inputSchema"]["properties"].update({
            "mode": {"type": "string", "enum": ["tracks", "isolated", "live"]},
            "skeleton_paths": {"type": "array", "maxItems": 16, "items": STRING},
            "max_bones": {"type": "integer", "minimum": 1, "maximum": 256},
            "properties": {"type": "array", "maxItems": 64, "items": {"type": "object", "properties": {"node_path": STRING, "property": STRING}, "required": ["node_path", "property"], "additionalProperties": False}},
        })
        entry["inputSchema"]["required"] = ["mode"]
