"""Bounded JSON and the schema subset used by the tool registry."""
from __future__ import annotations

import json
import math
import re


def validate_arguments(value, schema, path="arguments"):
    for keyword in ("allOf", "anyOf", "oneOf"):
        if keyword not in schema:
            continue
        successes = 0
        for candidate in schema[keyword]:
            try:
                validate_arguments(value, candidate, path)
                successes += 1
            except ValueError:
                pass
        if (keyword == "allOf" and successes != len(schema[keyword])) or (keyword == "anyOf" and successes == 0) or (keyword == "oneOf" and successes != 1):
            raise ValueError(f"{path} does not match {keyword} schema")
    if "not" in schema:
        try:
            validate_arguments(value, schema["not"], path)
        except ValueError:
            pass
        else:
            raise ValueError(f"{path} matches a forbidden schema")
    expected = schema.get("type")
    matches = {
        "object": isinstance(value, dict), "array": isinstance(value, list),
        "string": isinstance(value, str), "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "null": value is None,
    }
    if expected:
        types = expected if isinstance(expected, list) else [expected]
        if not any(matches.get(kind, False) for kind in types):
            raise ValueError(f"{path} must be {expected}")
    if "const" in schema and (value != schema["const"] or isinstance(value, bool) != isinstance(schema["const"], bool)):
        raise ValueError(f"{path} has the wrong discriminator")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} is not an allowed value")
    if isinstance(value, dict):
        if len(value) < schema.get("minProperties", 0) or len(value) > schema.get("maxProperties", 4096):
            raise ValueError(f"{path} has too many properties")
        for key in schema.get("required", []):
            if key not in value:
                raise ValueError(f"{path}.{key} is required")
        properties = schema.get("properties", {})
        for key, item in value.items():
            if key in properties:
                validate_arguments(item, properties[key], f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                raise ValueError(f"Unknown {path}.{key}")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_arguments(item, schema["additionalProperties"], f"{path}.{key}")
    elif isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", 4096):
            raise ValueError(f"{path} item count is outside the allowed range")
        for index, item in enumerate(value):
            validate_arguments(item, schema.get("items", {}), f"{path}[{index}]")
    elif isinstance(value, (float, int)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise ValueError(f"{path} must be finite; use a tagged float value")
        if value < schema.get("minimum", value) or value > schema.get("maximum", value):
            raise ValueError(f"{path} is outside the allowed range")
    elif isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", 1048576):
            raise ValueError(f"{path} length is outside the allowed range")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise ValueError(f"{path} does not match the required pattern")


def validate_json_budget(value, limit=4 * 1024 * 1024):
    remaining = 100000
    def visit(item, depth):
        nonlocal remaining
        remaining -= 1
        if depth > 32 or remaining < 0:
            raise ValueError("JSON structure budget exceeded")
        if isinstance(item, dict):
            if len(item) > 4096 or any(not isinstance(key, str) for key in item):
                raise ValueError("JSON object requires bounded string keys")
            for key, child in item.items():
                visit(key, depth + 1)
                visit(child, depth + 1)
        elif isinstance(item, list):
            if len(item) > 4096:
                raise ValueError("JSON array exceeds 4096 items")
            for child in item:
                visit(child, depth + 1)
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError("JSON number must be finite")
        elif isinstance(item, str) and len(item.encode("utf-8")) > limit:
            raise ValueError("JSON string exceeds byte budget")
    visit(value, 0)
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")) > limit:
        raise ValueError("JSON response/request byte budget exceeded")


def output_schema(required=(), properties=None):
    return {"type": "object", "required": list(required), "properties": properties or {}, "additionalProperties": True}


def configure_catalog(catalog):
    """Declare successful output shapes in the same registry exposed to clients."""
    groups = {
        "tool_names": ["capabilities_get"], "tools": ["tools_discover"], "connected": ["editor_status"],
        "job_id state artifacts": ["job_get", "job_wait", "job_cancel", "editor_start", "editor_close", "editor_restart", "run_start", "run_stop"],
        "events next_cursor dropped": ["events_read"],
        "artifact_id encoding data next_offset size complete": ["artifact_read"],
        "artifact_id width height driver target": ["capture_frame"], "sessions runs": ["run_list"],
        "scenes active": ["scene_list"], "scene_id revision": ["scene_create", "scene_open"],
        "saved path hash revision": ["scene_save"], "closed": ["scene_close"],
        "applied revision": ["scene_undo", "scene_redo"],
        "applied saved undoable operations revision": ["edit_batch"],
        "class properties methods signals": ["class_describe"], "settings": ["project_settings_get"],
        "applied saved keys": ["project_settings_set"], "resource": ["resource_load", "resource_create", "resource_duplicate"],
        "saved path": ["resource_save"], "released": ["object_release", "input_release_all"],
        "object properties": ["object_inspect"], "applied saved undoable": ["object_set_properties"],
        "value mutating undoable saved": ["object_call"], "nodes truncated": ["scene_tree", "object_query"],
        "scanned scanning": ["filesystem_scan"], "scripts runtime_updated": ["script_reload"],
        "animations truncated": ["animation_list"], "tracks truncated": ["animation_inspect", "animation_sample"],
        "applied saved undoable track revision": ["animation_edit"],
        "focused path": ["ui_focus"], "controls truncated": ["ui_inspect"],
        "condition satisfied frames clock": ["runtime_wait"], "completed frames released": ["input_sequence"],
        "injected": ["input_send"], "breakpoint_id registered scope": ["debugger_breakpoint_set", "debugger_breakpoint_clear"],
        "session_id active paused stack variables revision": ["debugger_state", "debugger_control"],
        "metrics process_frame physics_frame driver": ["monitors_read"], "session_id kind frames complete source": ["profiler_capture"],
        "path document_id source source_kind hash revision dirty disk_hash differs_from_disk": ["script_read"],
        "applied saved undoable document validation": ["script_patch"],
    }
    requirements = {"godot_" + name: required.split() for required, names in groups.items() for name in names}
    field_types = {
        **{key: "boolean" for key in ["connected", "applied", "saved", "undoable", "closed", "scanned", "scanning", "runtime_updated", "focused", "satisfied", "completed", "injected", "registered", "active", "paused", "complete", "dropped", "truncated", "dirty", "differs_from_disk"]},
        **{key: "integer" for key in ["next_cursor", "next_offset", "size", "width", "height", "revision", "released", "process_frame", "physics_frame", "track"]},
        **{key: "string" for key in ["job_id", "state", "artifact_id", "encoding", "data", "driver", "target", "scene_id", "path", "hash", "class", "condition", "clock", "breakpoint_id", "scope", "session_id", "kind", "source_kind", "document_id", "disk_hash", "validation"]},
        **{key: "array" for key in ["tool_names", "tools", "artifacts", "events", "sessions", "runs", "scenes", "properties", "methods", "signals", "keys", "operations", "nodes", "scripts", "animations", "tracks", "controls", "stack", "variables", "metrics"]},
        **{key: "object" for key in ["settings", "resource", "object", "document"]},
    }
    for entry in catalog:
        name = entry["name"]
        required = requirements.get(name, [])
        properties = {key: {"type": field_types[key]} for key in required if key in field_types}
        entry["outputSchema"] = output_schema(required, properties)
        if name == "godot_run_wait":
            entry["outputSchema"]["anyOf"] = [{"required": ["sessions"]}, {"required": ["session", "stage"]}, {"required": ["run_id", "reached", "stage"]}]
        if name == "godot_animation_sample":
            entry["outputSchema"] = {"type": "object", "oneOf": [output_schema(["tracks", "truncated", "sampling"]), output_schema(["mode", "samples", "sampling_stage", "side_effects", "playback_modified", "truncated"]) ]}
        if name == "godot_scene_list":
            entry["outputSchema"]["properties"]["active"] = {"type": ["object", "null"]}
        if name == "godot_input_sequence":
            entry["outputSchema"]["properties"]["released"] = {"type": "boolean"}
        if name == "godot_editor_select":
            entry["outputSchema"]["anyOf"] = [{"required": ["selected_panel"]}, {"required": ["selected_nodes"]}]
        if name == "godot_diagnostics_read":
            entry["outputSchema"]["anyOf"] = [{"required": ["entries"]}, {"required": ["artifacts", "state"]}]
        if name == "godot_resource_reimport":
            entry["outputSchema"] = output_schema(["job_id", "state", "artifacts"])
        entry["annotations"] = {"title": name.removeprefix("godot_").replace("_", " "), "openWorldHint": False}
    configure_inputs(catalog)
    configure_outputs(catalog)


def configure_outputs(catalog):
    """Constrain stable envelopes and records; reflected Variant payloads stay dynamic."""
    string = {"type": "string"}
    integer = {"type": "integer"}
    boolean = {"type": "boolean"}
    reference = {"type": "object", "anyOf": [
        {"required": ["_type", "ref", "class"], "properties": {"_type": {"const": "ObjectRef"}, "ref": string, "class": string}},
        {"required": ["_type", "reason"], "properties": {"_type": {"const": "truncated"}, "reason": string}},
    ]}
    def record(required, fields):
        return {"type": "object", "required": required.split(), "properties": fields}
    scene = record("scene_id revision path active dirty", {"scene_id": string, "revision": integer, "path": string, "active": boolean, "dirty": boolean})
    node = record("path name class groups object expired", {"path": string, "name": string, "class": string, "groups": {"type": "array", "items": string}, "object": {"anyOf": [reference, {"type": "null"}]}, "expired": boolean})
    prop = record("name type value", {"name": string, "type": integer})
    for tool in catalog:
        name, schema = tool["name"], tool["outputSchema"]
        fields = schema.setdefault("properties", {})
        if name == "godot_output_control":
            state = record("visible levels search collapse counts message_count stored_rows line_limit text_characters revision diagnostics editor_epoch action", {
                "visible": boolean, "levels": {"type": "array", "items": string}, "search": string, "collapse": boolean,
                "counts": record("info error warning editor", {key: integer for key in ["info", "error", "warning", "editor"]}),
                "message_count": integer, "stored_rows": integer, "line_limit": integer, "text_characters": integer,
                "revision": integer, "editor_epoch": string, "action": {"enum": ["get", "configure", "clear"]},
                "diagnostics": record("latest_cursor oldest_cursor retained_count history_generation cleared_through_cursor", {key: integer for key in ["latest_cursor", "oldest_cursor", "retained_count", "history_generation", "cleared_through_cursor"]}),
            })
            read = record("text next_offset total_characters has_more revision editor_epoch action", {"text": string, "next_offset": integer, "total_characters": integer, "has_more": boolean, "revision": integer, "editor_epoch": string, "action": {"const": "read"}})
            tool["outputSchema"] = {"type": "object", "oneOf": [state, read]}
        if name in {"godot_scene_tree", "godot_object_query"}:
            schema["required"] += ["snapshot", "next_offset", "total", "has_more", "source_truncated"]
            fields.update({"nodes": {"type": "array", "maxItems": 1024, "items": node}, "snapshot": string, "next_offset": integer, "total": integer, "has_more": boolean, "source_truncated": boolean})
        if name == "godot_scene_list":
            fields["scenes"] = {"type": "array", "items": scene}
        if name == "godot_object_inspect":
            fields.update({"object": reference, "properties": {"type": "array", "maxItems": 256, "items": prop}})
        if name in {"godot_resource_load", "godot_resource_create", "godot_resource_duplicate"}:
            fields["resource"] = reference
        if name in {"godot_job_get", "godot_job_wait", "godot_job_cancel", "godot_editor_start", "godot_editor_close", "godot_editor_restart", "godot_run_start", "godot_run_stop", "godot_resource_reimport"}:
            fields.update({"job_id": string, "state": {"enum": ["queued", "running", "succeeded", "failed", "cancelled", "timed_out"]}, "sequence": integer, "artifacts": {"type": "array", "items": string}, "result": {"type": "object"}, "error": string})
            schema["allOf"] = [{"anyOf": [{"properties": {"state": {"enum": ["queued", "running", "cancelled"]}}}, {"required": ["result"], "properties": {"state": {"const": "succeeded"}}}, {"required": ["error"], "properties": {"state": {"enum": ["failed", "timed_out"]}}}]}]
        if name == "godot_events_read":
            fields["events"] = {"type": "array", "maxItems": 2048, "items": record("sequence kind", {"sequence": integer, "kind": {"enum": ["job_state", "run_stage", "run_exited"]}, "job_id": string, "session_id": string})}
        if name == "godot_artifact_read":
            fields.update({"encoding": {"const": "base64"}, "mime_type": string, "sha256": {"type": ["string", "null"]}})
        if name == "godot_script_patch":
            fields["document"] = record("document_id path source_kind hash revision dirty", {"document_id": string, "path": string, "source_kind": {"const": "editor"}, "hash": string, "revision": integer, "dirty": boolean})


def discriminated(properties, field, variants, common_required=()):
    alternatives = []
    for discriminator, (allowed, required) in variants.items():
        selected = {key: properties[key] for key in set(allowed.split()) | {field} | set(common_required)}
        selected[field] = {"const": discriminator}
        alternatives.append({"type": "object", "properties": selected, "required": list(common_required) + [field] + required.split(), "additionalProperties": False})
    return {"type": "object", "oneOf": alternatives}


def configure_inputs(catalog):
    by_name = {entry["name"]: entry for entry in catalog}
    output = by_name["godot_output_control"]
    output["inputSchema"] = discriminated(output["inputSchema"]["properties"], "action", {
        "get": ("", ""),
        "configure": ("visible collapse search levels", ""),
        "read": ("offset max_chars expected_revision editor_epoch", ""),
        "clear": ("scope editor_epoch through_cursor", "editor_epoch"),
    })
    get, configure, read, clear = output["inputSchema"]["oneOf"]
    get["required"].remove("action")
    configure["anyOf"] = [{"required": [key]} for key in ["visible", "collapse", "search", "levels"]]
    read["anyOf"] = [{"properties": {"offset": {"const": 0}}}, {"required": ["expected_revision", "editor_epoch"]}]
    clear["oneOf"] = [
        {"properties": {"scope": {"const": "panel"}}, "not": {"required": ["through_cursor"]}},
        {"properties": {"scope": {"enum": ["diagnostics", "all"]}}, "required": ["scope", "through_cursor"]},
    ]
    by_name["godot_tools_discover"]["inputSchema"]["properties"].update({"domains": {"type": "array", "minItems": 1, "maxItems": 32, "items": {"type": "string", "minLength": 1}}, "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 128}, "include_schema": {"type": "boolean"}})
    by_name["godot_tools_discover"]["inputSchema"]["properties"]["tier"] = {"type": "string", "enum": ["core", "extended"]}
    by_name["godot_tools_discover"]["inputSchema"]["properties"]["limit"]["default"] = 20
    by_name["godot_tools_discover"]["inputSchema"]["properties"]["include_schema"]["default"] = False
    by_name["godot_diagnostics_read"]["inputSchema"]["properties"].update({"source": {"type": "string", "enum": ["editor", "runtime"]}, "session_id": {"type": "string", "minLength": 1}})
    by_name["godot_resource_save"]["inputSchema"]["properties"]["expected_disk_hash"] = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
    by_name["godot_job_wait"]["inputSchema"]["properties"].update({"until": {"type": "string", "enum": ["terminal", "changed"]}, "after_sequence": {"type": "integer", "minimum": 0}})
    by_name["godot_job_wait"]["inputSchema"]["allOf"] = [{"anyOf": [{"properties": {"until": {"const": "terminal"}}}, {"properties": {"until": {"const": "changed"}}, "required": ["until", "after_sequence"]}]}]
    by_name["godot_events_read"]["inputSchema"]["properties"].update({"job_id": {"type": "string", "minLength": 1}, "session_id": {"type": "string", "minLength": 1}, "kinds": {"type": "array", "minItems": 1, "maxItems": 3, "items": {"type": "string", "enum": ["job_state", "run_stage", "run_exited"]}}})
    input_tool = by_name["godot_input_send"]
    props = input_tool["inputSchema"]["properties"]
    modifiers = " shift ctrl alt meta"
    variants = {
        "key": ("pressed echo keycode physical_keycode unicode" + modifiers, ""),
        "text": ("text", "text"),
        "action_state": ("action pressed strength", "action"),
        "action_event": ("action pressed strength", "action"),
        "mouse_button": ("button pressed x y" + modifiers, "button"),
        "mouse_motion": ("x y delta_x delta_y" + modifiers, ""),
        "wheel": ("direction factor x y" + modifiers, "direction"),
        "touch": ("index pressed x y", "index"),
        "touch_motion": ("index x y delta_x delta_y", "index"),
        "joy_button": ("device joy_button pressed strength", "joy_button"),
        "joy_motion": ("device axis value", "axis value"),
    }
    input_tool["inputSchema"] = discriminated(props, "kind", variants, ["session_id"])
    key_schema = input_tool["inputSchema"]["oneOf"][0]
    key_schema["anyOf"] = [{"required": [key], "properties": {key: {"minimum": 1}}} for key in ["keycode", "physical_keycode", "unicode"]]
    sequence_event = discriminated(props, "kind", variants)
    sequence_event["oneOf"][0]["anyOf"] = key_schema["anyOf"]
    by_name["godot_input_sequence"]["inputSchema"]["properties"]["steps"]["items"]["properties"]["event"] = sequence_event

    batch = by_name["godot_edit_batch"]["inputSchema"]["properties"]["operations"]
    batch["items"] = discriminated(batch["items"]["properties"], "op", {
        "create": ("id name type parent_path", "name"),
        "instance_scene": ("id name path parent_path", "name path"),
        "duplicate": ("id node_path name parent_path", "node_path name"),
        "delete": ("node_path", "node_path"),
        "rename": ("node_path name", "node_path name"),
        "reparent": ("node_path parent_path index keep_global_transform", "node_path parent_path"),
        "groups": ("node_path add remove", "node_path"),
        "set": ("node_path changes", "node_path changes"),
        "attach": ("node_path script_path", "node_path script_path"),
        "connect": ("node_path signal_name receiver_path method binds unbind flags", "node_path signal_name receiver_path method"),
        "disconnect": ("node_path signal_name receiver_path method binds unbind", "node_path signal_name receiver_path method"),
    })
    for alternative in batch["items"]["oneOf"]:
        if alternative["properties"]["op"]["const"] == "attach":
            alternative["properties"]["script_path"] = {"type": ["string", "null"], "minLength": 1}
        if alternative["properties"]["op"]["const"] == "set":
            alternative["properties"]["changes"].update({"minProperties": 1, "maxProperties": 256})

    wait = by_name["godot_runtime_wait"]["inputSchema"]
    wait_props = wait["properties"]
    conditions = discriminated(wait_props, "condition", {
        "frames": ("frames clock timeout_ms", ""),
        "property": ("node_path object_ref property comparison value timeout_ms", "property value"),
        "signal": ("node_path object_ref signal_name timeout_ms", "signal_name"),
    }, ["session_id"])
    for index, alternative in enumerate(conditions["oneOf"]):
        if index == 0:
            alternative["required"].remove("condition")
        else:
            alternative["oneOf"] = [{"required": ["node_path"], "not": {"required": ["object_ref"]}}, {"required": ["object_ref"], "not": {"required": ["node_path"]}}]
    by_name["godot_runtime_wait"]["inputSchema"] = conditions

    sample = by_name["godot_animation_sample"]
    sample_props = sample["inputSchema"]["properties"]
    sample["inputSchema"] = discriminated(sample_props, "mode", {
        "tracks": ("context scene_id session_id node_path object_ref animation_name max_keys times", "times"),
        "isolated": ("context scene_id session_id node_path object_ref animation_name times skeleton_paths max_bones properties", "animation_name times"),
        "live": ("context scene_id session_id node_path object_ref skeleton_paths max_bones properties", ""),
    })
    sample["inputSchema"]["oneOf"][1]["properties"]["times"] = {"type": "array", "minItems": 1, "maxItems": 16, "items": {"type": "number", "minimum": 0}}

    patch = by_name["godot_script_patch"]
    props = patch["inputSchema"]["properties"]
    patch["inputSchema"] = discriminated(props, "action", {
        "apply": ("expected_revision edits", "edits"),
        "undo": ("expected_revision", ""), "redo": ("expected_revision", ""),
    }, ["document_id", "expected_hash"])
    patch["inputSchema"]["oneOf"][0]["required"].remove("action")

    run_wait = by_name["godot_run_wait"]
    props = run_wait["inputSchema"]["properties"]
    run_wait["inputSchema"] = {"type": "object", "oneOf": [
        {"type": "object", "properties": {key: props[key] for key in ["run_id", "stage", "timeout_ms"]}, "required": ["run_id", "stage"], "additionalProperties": False},
        {"type": "object", "properties": {key: props[key] for key in ["minimum_sessions", "timeout_ms"]}, "additionalProperties": False},
    ]}
    animation = by_name["godot_animation_edit"]
    props = animation["inputSchema"]["properties"]
    common = ["scene_id", "expected_revision"]
    variants = {
        "add_track": ("node_path object_ref animation_name track_type path", "track_type path"),
        "remove_track": ("node_path object_ref animation_name track", "track"),
        "set_key": ("node_path object_ref animation_name track time value", "track time value"),
        "remove_key": ("node_path object_ref animation_name track key", "track key"),
        "set_metadata": ("node_path object_ref animation_name length loop_mode", ""),
    }
    animation["inputSchema"] = discriminated(props, "operation", variants, common)
    by_name["godot_object_call"]["inputSchema"]["properties"].pop("animation_name", None)
    by_name["godot_object_call"]["inputSchema"]["properties"].pop("max_keys", None)
    unified = {"godot_object_inspect", "godot_object_set_properties", "godot_object_release", "godot_object_call", "godot_scene_tree", "godot_object_query", "godot_animation_list", "godot_animation_inspect", "godot_animation_sample"}
    for entry in catalog:
        if entry["name"] in unified:
            required = [] if entry["name"] == "godot_object_release" else ["scene_id"]
            if entry["name"] == "godot_object_set_properties":
                required.append("expected_revision")
            entry["inputSchema"].setdefault("allOf", []).append({"oneOf": [
                {"properties": {"context": {"const": "editor"}}, "required": required, "not": {"required": ["session_id"]}},
                {"properties": {"context": {"const": "runtime"}}, "required": ["context", "session_id"], "not": {"anyOf": [{"required": ["scene_id"]}, {"required": ["expected_revision"]}]}},
            ]})
            entry["inputSchema"].setdefault("allOf", []).append({"not": {"required": ["node_path", "object_ref"]}})
    for entry in catalog:
        properties = entry["inputSchema"].get("properties", {})
        if "changes" in properties:
            properties["changes"].update({"minProperties": 1, "maxProperties": 256})
        if "node_path" in properties and "object_ref" in properties:
            entry["inputSchema"]["not"] = {"required": ["node_path", "object_ref"]}
