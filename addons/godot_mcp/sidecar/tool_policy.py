"""Small default catalog; stateful editor operations remain discoverable on demand."""

CORE_TOOL_NAMES = frozenset({
    "godot_capabilities_get", "godot_tools_discover", "godot_tools_call",
    "godot_editor_status", "godot_diagnostics_read", "godot_output_control", "godot_job_wait",
    "godot_scene_list", "godot_scene_tree", "godot_filesystem_scan",
    "godot_editor_start", "godot_editor_close", "godot_editor_restart",
    "godot_extension_reload",
    "godot_run_start", "godot_run_list", "godot_run_stop", "godot_run_wait",
    "godot_runtime_wait", "godot_object_inspect", "godot_input_send",
    "godot_input_release_all", "godot_capture_frame",
    "godot_debugger_state", "godot_debugger_control",
})


def select_tools(catalog, toolset="core"):
    if toolset not in {"core", "all"}:
        raise ValueError("Unknown toolset; use core or all")
    return catalog if toolset == "all" else [tool for tool in catalog if tool["name"] in CORE_TOOL_NAMES]
