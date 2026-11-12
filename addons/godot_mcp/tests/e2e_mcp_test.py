#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import contextlib
import json
import os
import selectors
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from process_cleanup import cleanup_fixture


class MCPClient:
    def __init__(self, sidecar: Path, project: Path | None = None, godot: Path | None = None):
        self.process = subprocess.Popen(
            ["python3", str(sidecar), *(["--project", str(project)] if project else []), *(["--godot", str(godot)] if godot else [])],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.next_id = 1
        self.responses = {}
        self.stdout_buffer = b""
        self.request_tools = {}
        self.verified_tools = set()

    def close(self) -> None:
        if self.process.stdin:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=3)

    def send_request(self, method: str, params: dict[str, Any] | None = None) -> int:
        request_id = self.next_id
        self.next_id += 1
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        if method == "tools/call":
            self.request_tools[request_id] = params["name"]
        assert self.process.stdin
        self.process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        return request_id

    def receive_request(self, request_id: int) -> dict[str, Any]:
        assert self.process.stdout
        deadline = time.monotonic() + 20
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            while request_id not in self.responses:
                if b"\n" not in self.stdout_buffer:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(timeout=remaining):
                        raise TimeoutError(f"MCP request timed out: {request_id}")
                    chunk = os.read(self.process.stdout.fileno(), 65536)
                    if not chunk:
                        stderr = self.process.stderr.read() if self.process.stderr else ""
                        raise RuntimeError(f"MCP sidecar exited without a response: {stderr}")
                    self.stdout_buffer += chunk
                    continue
                line, self.stdout_buffer = self.stdout_buffer.split(b"\n", 1)
                response = json.loads(line)
                self.responses[response["id"]] = response
        response = self.responses.pop(request_id)
        if "error" in response:
            raise AssertionError(f"MCP JSON-RPC error: {response['error']}")
        if request_id in self.request_tools and not response["result"].get("isError"):
            self.verified_tools.add(self.request_tools.pop(request_id))
        return response["result"]

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.receive_request(self.send_request(method, params))

    def call(self, name: str, arguments: dict[str, Any] | None = None, allow_error: bool = False) -> Any:
        result = self.request("tools/call", {"name": name, "arguments": arguments or {}})
        if result.get("isError") and not allow_error:
            raise AssertionError(f"Tool {name} failed: {result['content']}")
        return result.get("structuredContent", result)


def submit_edit(client, operation, arguments, allow_error=False):
    target = {key: arguments[key] for key in ["scene_id", "expected_revision"]}
    change = {key: value for key, value in arguments.items() if key not in target}
    return client.call("godot_edit_batch", {**target, "operations": [{"op": operation, **change}]}, allow_error=allow_error)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sidecar", type=Path, required=True)
    parser.add_argument("--require-capture", action="store_true")
    parser.add_argument("--hidden-gpu", action="store_true")
    parser.add_argument("--visible", action="store_true", help="Explicitly permit normal development test windows.")
    parser.add_argument("--project", type=Path)
    parser.add_argument("--task-godot", type=Path)
    parser.add_argument("--godot", type=Path, help="Launch an isolated fixture editor and clean it up after testing.")
    args = parser.parse_args()

    if args.godot:
        return run_isolated(args)
    client = MCPClient(args.sidecar.resolve(), args.project, args.task_godot)
    session_id = None

    def editor_state():
        return client.call("godot_editor_status")["state"]

    def active_document():
        return client.call("godot_scene_list")["active"]

    def edit(name, arguments=None, allow_error=False):
        current = active_document()
        return client.call(name, {"scene_id": current["scene_id"], "expected_revision": current["revision"], **(arguments or {})}, allow_error=allow_error)

    def edit_operation(operation, arguments=None, allow_error=False, **named_arguments):
        current = active_document()
        return submit_edit(client, operation, {"scene_id": current["scene_id"], "expected_revision": current["revision"], **(arguments or {}), **named_arguments}, allow_error)

    def open_documents():
        return client.call("godot_scene_list")

    def open_document(arguments):
        return client.call("godot_scene_open", {"path": arguments["scene_path"]})

    def close_document(arguments, allow_error=False):
        return edit("godot_scene_close", {"unsaved": "discard" if arguments.get("force") else "reject"}, allow_error)

    def save_document():
        return edit("godot_scene_save")

    def undo_document():
        return edit("godot_scene_undo")

    def redo_document():
        return edit("godot_scene_redo")

    def edited_tree(arguments=None):
        return client.call("godot_scene_tree", {"scene_id": active_document()["scene_id"]})

    def inspect_editor(arguments, allow_error=False):
        return client.call("godot_object_inspect", {"scene_id": active_document()["scene_id"], **arguments}, allow_error=allow_error)

    def set_editor(arguments, allow_error=False):
        return edit("godot_object_set_properties", {"node_path": arguments["node_path"], "changes": {arguments["property"]: arguments["value"]}}, allow_error)

    def create_node(arguments, allow_error=False):
        operations = [{"op": "create", "id": "created", "name": arguments["name"], "type": arguments.get("type", "Node3D" if arguments.get("script_path") else "Node"), "parent_path": arguments.get("parent_path", ".")}]
        if arguments.get("script_path"):
            operations.append({"op": "attach", "node_path": "$created", "script_path": arguments["script_path"]})
        if arguments.get("properties"):
            operations.append({"op": "set", "node_path": "$created", "changes": arguments["properties"]})
        return edit("godot_edit_batch", {"operations": operations}, allow_error)

    def delete_node(arguments, allow_error=False):
        return edit_operation("delete", arguments, allow_error)

    def runtime_tree_read(arguments=None):
        return client.call("godot_scene_tree", {"context": "runtime", "session_id": session_id})

    def inspect_runtime(arguments):
        return client.call("godot_object_inspect", {"context": "runtime", "session_id": session_id, **arguments})

    def set_runtime(arguments):
        return client.call("godot_object_set_properties", {"context": "runtime", "session_id": session_id, "node_path": arguments["node_path"], "changes": {arguments["property"]: arguments["value"]}})

    def send_action(arguments):
        return client.call("godot_input_send", {"session_id": session_id, "kind": "action_state", **arguments})

    def send_mouse(arguments):
        return client.call("godot_input_send", {"session_id": session_id, **arguments, "kind": {"button": "mouse_button", "motion": "mouse_motion", "wheel": "wheel"}[arguments["kind"]]})

    def wait_frames(arguments):
        return client.call("godot_runtime_wait", {"session_id": session_id, **arguments})

    def read_logs(arguments=None, allow_error=False):
        return client.call("godot_diagnostics_read", arguments or {}, allow_error=allow_error)

    def finish_job(value):
        completed = client.call("godot_job_wait", {"job_id": value["job_id"], "timeout_ms": 15000})
        assert completed["state"] == "succeeded", completed
        return completed["result"]

    report: dict[str, Any] = {}
    try:
        initialized = client.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "e2e-test", "version": "1"}})
        assert initialized["serverInfo"]["name"] == "godot-mcp"

        default_tools = client.request("tools/list")["tools"]
        assert len(default_tools) == 24 and all(tool["_meta"]["godot/tier"] == "core" for tool in default_tools)
        tools = client.call("godot_tools_discover", {"include_schema": True, "limit": 128})["tools"]
        tool_names = {tool["name"] for tool in tools}
        required = {"godot_editor_status", "godot_scene_list", "godot_scene_open", "godot_scene_close", "godot_run_start", "godot_run_stop", "godot_scene_tree", "godot_object_inspect", "godot_object_set_properties", "godot_input_send"}
        assert required <= tool_names and len(tools) == 62, tool_names
        report["tool_count"] = len(tools)
        report["default_tool_count"] = len(default_tools)

        initial_state_result = client.request("tools/call", {"name": "godot_editor_status", "arguments": {}})
        state = initial_state_result["structuredContent"]["state"]
        assert initial_state_result["_meta"]["godot"]["scope"] == "sidecar"
        assert initial_state_result["_meta"]["godot"]["protocol"] == 2
        assert "4.7.2-stable" in state["godot_version"]
        report["project_path"] = state["project_path"]

        capabilities = client.call("godot_capabilities_get")
        assert capabilities["editor"]["package"]["verified"] and all(capabilities["editor"]["native_api"].values()), capabilities
        summary = client.call("godot_tools_discover", {"domains": ["scene"], "include_schema": False, "limit": 2})
        assert summary["has_more"] and len(summary["tools"]) == 2 and all("inputSchema" not in tool for tool in summary["tools"]), summary
        external_tools = {"godot_image_compare", "godot_asset_bake", "godot_export_run", "godot_script_run", "godot_tests_run", "godot_script_validate", "godot_files_search", "godot_resource_dependencies", "godot_scene_validate", "godot_script_create"}
        assert not external_tools.intersection(capabilities["tool_names"])
        assert "godot_run_start" in capabilities["tool_names"]
        assert client.call("godot_tools_discover", {"query": "godot_job_wait"})["tools"][0]["name"] == "godot_job_wait"
        assert client.call("godot_tools_call", {"tool_name": "godot_editor_status", "arguments": {}})["state"]["project_path"] == state["project_path"]
        assert client.call("godot_editor_status")["connected"]
        if args.task_godot:
            variant = subprocess.run([str(args.task_godot), "--headless", "--path", str(args.project), "--script", "res://value_protocol.gd"], capture_output=True, text=True, timeout=15)
            assert variant.returncode == 0 and "MCP_VARIANT_PROTOCOL_OK" in variant.stdout, (variant.stdout, variant.stderr)
            report["variant_codec_standalone_test"] = True

        scenes = open_documents()
        assert "res://main.tscn" in [d["path"] for d in scenes["scenes"]]
        assert scenes["active"]["path"] == "res://main.tscn"
        closed = close_document({"scene_path": "res://main.tscn"})
        assert closed["closed"]
        scenes = open_documents()
        assert "res://main.tscn" not in [d["path"] for d in scenes["scenes"]]
        opened = open_document({"scene_path": "res://main.tscn"})
        assert opened["scene_id"]
        report["scene_file_coordination"] = True

        assert client.call("godot_editor_select", {"panel": "3d"})["selected_panel"] == "3d"

        edited = edited_tree({"max_depth": 4})
        root_node = next(node for node in edited["nodes"] if node["name"] == "MCPTest")
        editor_path = root_node["path"]
        inspected_editor = inspect_editor({"node_path": editor_path, "properties": ["health"]})
        editor_values = {item["name"]: item["value"] for item in inspected_editor["properties"]}
        assert editor_values["health"] == 100
        changed_editor = set_editor({"node_path": editor_path, "property": "health", "value": 77})
        assert changed_editor["applied"]
        unsafe_close = close_document({"scene_path": "res://main.tscn"}, allow_error=True)
        assert unsafe_close["isError"] is True
        report["unsaved_scene_protected"] = True
        set_editor({"node_path": editor_path, "property": "health", "value": 100})
        report["editor_property_write"] = True
        save_document()

        created = create_node({
            "name": "SpawnPoint", "script_path": "res://spawn_point.gd",
            "properties": {"archetype_id": 17, "spawn_count": 3},
        })
        assert created["references"]["created"]["class"] == "Node3D"
        create_node({"name": "Child", "parent_path": "SpawnPoint", "type": "Node3D"})
        duplicate = create_node({"name": "SpawnPoint"}, allow_error=True)
        assert duplicate["isError"]
        delete_node({"node_path": "SpawnPoint"})
        undo_document()
        restored = edited_tree()["nodes"]
        assert any(node["name"] == "Child" for node in restored)
        redo_document()
        assert not any(node["name"] == "SpawnPoint" for node in edited_tree()["nodes"])
        undo_document()
        save_document()
        close_document({"scene_path": "res://main.tscn"})
        open_document({"scene_path": "res://main.tscn"})
        saved = inspect_editor({"node_path": "SpawnPoint", "properties": ["archetype_id", "spawn_count"]})
        assert {item["name"]: item["value"] for item in saved["properties"]} == {"archetype_id": 17, "spawn_count": 3}
        assert any(node["name"] == "Child" for node in edited_tree()["nodes"])
        root_delete = delete_node({"node_path": "."}, allow_error=True)
        assert root_delete["isError"]
        escaped = inspect_editor({"node_path": "/root"}, allow_error=True)
        assert escaped["isError"]
        report["node_edit_undo_redo_persistence"] = True

        ordinary = create_node({
            "name": "OrdinaryScript", "script_path": "res://ordinary_script.gd",
            "properties": {"value": 73},
        })
        assert ordinary["references"]["created"]["class"] == "Node3D"
        save_document()
        close_document({"scene_path": "res://main.tscn"})
        open_document({"scene_path": "res://main.tscn"})
        ordinary_node = inspect_editor({
            "node_path": "OrdinaryScript", "properties": ["value"],
        })
        assert next(item["value"] for item in ordinary_node["properties"] if item["name"] == "value") == 73
        report["ordinary_script_persistence"] = True

        buffer = client.call("godot_script_read", {"path": "res://ordinary_script.gd", "open_if_needed": True})
        assert buffer["source_kind"] == "editor" and "value := 42" in buffer["source"], buffer
        changed_buffer = client.call("godot_script_patch", {"document_id": buffer["document_id"], "expected_hash": buffer["hash"], "expected_revision": buffer["revision"], "edits": [{"before": "value := 42", "after": "value := 43"}]})["document"]
        assert changed_buffer["dirty"] and changed_buffer["differs_from_disk"] and "value := 43" in changed_buffer["source"], changed_buffer
        assert "value := 42" in (Path(state["project_path"]) / "ordinary_script.gd").read_text()
        stale_buffer = client.call("godot_script_patch", {"document_id": buffer["document_id"], "expected_hash": buffer["hash"], "edits": [{"before": "value := 43", "after": "value := 44"}]}, allow_error=True)
        assert "REVISION_CONFLICT" in json.dumps(stale_buffer), stale_buffer
        restored_buffer = client.call("godot_script_patch", {"document_id": buffer["document_id"], "expected_hash": changed_buffer["hash"], "action": "undo"})["document"]
        assert restored_buffer["hash"] == buffer["hash"], restored_buffer
        changed_buffer = client.call("godot_script_patch", {"document_id": buffer["document_id"], "expected_hash": restored_buffer["hash"], "action": "redo"})["document"]
        assert "value := 43" in changed_buffer["source"] and "res://ordinary_script.gd" in editor_state()["unsaved_scripts"]
        report["editor_script_buffer_patch_undo_redo"] = True


        assert client.call("godot_class_describe", {"class_name": "Theme"})["class"] == "Theme"
        settings = client.call("godot_project_settings_get", {"keys": ["application/config/name"]})
        assert settings["settings"]["application/config/name"]["exists"]
        assert "extends Node3D" in (Path(state["project_path"]) / "ordinary_script.gd").read_text(encoding="utf-8")
        theme = client.call("godot_resource_create", {"class_name": "Theme"})["resource"]
        duplicate_theme = client.call("godot_resource_duplicate", {"object_ref": theme["ref"], "deep": True})["resource"]
        saved_theme = client.call("godot_resource_save", {"object_ref": theme["ref"], "path": "res://automation_theme.tres"})
        conflict = client.call("godot_resource_save", {"object_ref": theme["ref"], "path": "res://automation_theme.tres", "overwrite": True, "expected_disk_hash": "0" * 64}, allow_error=True)
        assert "DISK_CONFLICT" in json.dumps(conflict), conflict
        client.call("godot_resource_save", {"object_ref": theme["ref"], "path": "res://automation_theme.tres", "overwrite": True, "expected_disk_hash": saved_theme["hash"]})
        other_theme = client.call("godot_resource_duplicate", {"object_ref": theme["ref"], "deep": True})["resource"]
        takeover = client.call("godot_resource_save", {"object_ref": other_theme["ref"], "path": "res://automation_theme.tres", "overwrite": True, "expected_disk_hash": saved_theme["hash"]}, allow_error=True)
        assert "RESOURCE_IDENTITY_CONFLICT" in json.dumps(takeover), takeover
        script_resource = client.call("godot_resource_load", {"path": "res://main.gd"})["resource"]
        denied_source = edit("godot_object_set_properties", {"object_ref": script_resource["ref"], "changes": {"source_code": "extends Node"}}, allow_error=True)
        assert "DOCUMENT_EDIT_REQUIRED" in json.dumps(denied_source), denied_source
        report["resource_cache_identity_and_source_document_guards"] = True
        save_document()
        theme_size = inspect_editor({"object_ref": theme["ref"], "properties": ["default_font_size"]})["properties"][0]["value"]
        edit("godot_object_set_properties", {"object_ref": theme["ref"], "changes": {"default_font_size": 23}})
        assert editor_state()["unsaved_editor_resources"]["global_history_unsaved"]
        assert not next(item for item in open_documents()["scenes"] if item["scene_id"] == active_document()["scene_id"])["dirty"]
        close_state = editor_state()
        refused_close = client.call("godot_editor_close", {"editor_epoch": close_state["editor_epoch"], "expected_document_revision": close_state["document_revision"], "unsaved": "reject"})
        refused_close = client.call("godot_job_wait", {"job_id": refused_close["job_id"], "timeout_ms": 5000})
        assert refused_close["state"] == "failed" and "UNSAVED_EDITOR_RESOURCES" in json.dumps(refused_close), refused_close
        undo_document()
        assert inspect_editor({"object_ref": theme["ref"], "properties": ["default_font_size"]})["properties"][0]["value"] == theme_size
        assert not editor_state()["unsaved_editor_resources"]["global_history_unsaved"]
        report["shared_resource_global_history_and_close_protection"] = True
        loaded_theme = client.call("godot_resource_load", {"path": "res://automation_theme.tres"})["resource"]
        create_node({"name": "ThemeControl", "type": "Control"})
        document = client.call("godot_scene_list")["active"]
        target = {"scene_id": document["scene_id"], "node_path": "ThemeControl"}
        client.call("godot_object_set_properties", {**target, "expected_revision": document["revision"], "changes": {"theme": loaded_theme}})
        inspected = client.call("godot_object_inspect", {**target, "properties": ["theme"]})
        assert inspected["properties"][0]["value"]["class"] == "Theme"
        stale = client.call("godot_object_set_properties", {**target, "expected_revision": document["revision"], "changes": {"visible": False}}, allow_error=True)
        assert stale["isError"]
        document = client.call("godot_scene_list")["active"]
        submit_edit(client, "rename", {**target, "expected_revision": document["revision"], "name": "StyledControl"})
        document = client.call("godot_scene_list")["active"]
        submit_edit(client, "attach", {"scene_id": document["scene_id"], "expected_revision": document["revision"], "node_path": "OrdinaryScript", "script_path": None})
        document = client.call("godot_scene_list")["active"]
        submit_edit(client, "attach", {"scene_id": document["scene_id"], "expected_revision": document["revision"], "node_path": "OrdinaryScript", "script_path": "res://ordinary_script.gd"})
        save_document()
        close_document({"scene_path": "res://main.tscn"})
        open_document({"scene_path": "res://main.tscn"})
        document = client.call("godot_scene_list")["active"]
        persisted = client.call("godot_object_inspect", {"scene_id": document["scene_id"], "node_path": "StyledControl", "properties": ["theme"]})
        assert persisted["properties"][0]["value"]["class"] == "Theme"
        client.call("godot_object_release", {"object_refs": [duplicate_theme["ref"]]})
        expired = client.call("godot_resource_duplicate", {"object_ref": duplicate_theme["ref"]}, allow_error=True)
        assert expired["isError"]
        report["typed_editor_resources_and_revisions"] = True
        inherited = client.call("godot_scene_open", {"path": "res://inherited_scene.tscn"})
        for operation, extra in [("delete", {}), ("rename", {"name": "CannotRename"}), ("reparent", {"parent_path": "."})]:
            rejected = submit_edit(client, operation, {"scene_id": inherited["scene_id"], "expected_revision": inherited["revision"], "node_path": "InheritedChild", **extra}, allow_error=True)
            assert rejected["isError"] and "inherit" in json.dumps(rejected).lower(), rejected
        client.call("godot_scene_close", {"scene_id": inherited["scene_id"], "expected_revision": inherited["revision"]})
        client.call("godot_scene_open", {"path": "res://main.tscn"})
        imported = edit("godot_edit_batch", {"operations": [{"op": "instance_scene", "path": "res://imported_animation.gltf", "name": "ImportedAnimation"}]})
        imported_target = {"scene_id": active_document()["scene_id"], "node_path": "ImportedAnimation/AnimationPlayer"}
        imported_names = client.call("godot_animation_list", imported_target)["animations"]
        assert any(item["name"] == "lift" for item in imported_names), imported_names
        imported_clip = {**imported_target, "animation_name": "lift"}
        assert client.call("godot_animation_inspect", imported_clip)["tracks"]
        refused = client.call("godot_animation_edit", {**imported_clip, "expected_revision": active_document()["revision"], "operation": "set_metadata", "length": 2.0}, allow_error=True)
        assert "IMPORTED_RESOURCE" in json.dumps(refused), refused
        edit_operation("delete", {"node_path": "ImportedAnimation"})
        report["inherited_structure_protected_and_imported_animation_read"] = True


        fresh = client.call("godot_scene_create", {"root_type": "Node3D", "root_name": "CreatedScene"})
        rejected = client.call("godot_scene_close", {"scene_id": fresh["scene_id"], "expected_revision": fresh["revision"]}, allow_error=True)
        assert rejected["isError"]
        create_node({"name": "OriginalChild", "type": "Node3D"})
        document = client.call("godot_scene_list")["active"]
        renamed = submit_edit(client, "rename", {"scene_id": document["scene_id"], "expected_revision": document["revision"], "node_path": "OriginalChild", "name": "RenamedChild"})
        undone = client.call("godot_scene_undo", {"scene_id": document["scene_id"], "expected_revision": renamed["revision"]})
        assert any(n["name"] == "OriginalChild" for n in edited_tree()["nodes"])
        redone = client.call("godot_scene_redo", {"scene_id": document["scene_id"], "expected_revision": undone["revision"]})
        saved = client.call("godot_scene_save", {"scene_id": document["scene_id"], "expected_revision": redone["revision"], "path": "res://created_scene.tscn"})
        assert saved["saved"] and saved["hash"]
        client.call("godot_scene_close", {"scene_id": document["scene_id"], "expected_revision": saved["revision"]})
        reopened = client.call("godot_scene_open", {"path": "res://created_scene.tscn"})
        assert any(n["name"] == "RenamedChild" for n in edited_tree()["nodes"])
        client.call("godot_scene_close", {"scene_id": reopened["scene_id"], "expected_revision": reopened["revision"]})
        client.call("godot_scene_open", {"path": "res://main.tscn"})
        main_document = client.call("godot_scene_list")["active"]
        other_document = client.call("godot_scene_open", {"path": "res://created_scene.tscn"})
        main_document = next(item for item in client.call("godot_scene_list")["scenes"] if item["scene_id"] == main_document["scene_id"])
        main_read = client.call("godot_scene_tree", {"scene_id": main_document["scene_id"]})
        assert any(n["name"] == "MCPTest" for n in main_read["nodes"]), main_read
        other_before = client.call("godot_object_inspect", {"scene_id": other_document["scene_id"], "node_path": ".", "properties": ["position"]})
        main_change = client.call("godot_object_set_properties", {"scene_id": main_document["scene_id"], "expected_revision": main_document["revision"], "node_path": ".", "changes": {"health": 101}})
        assert editor_state()["edited_scene"] == "res://created_scene.tscn"
        assert client.call("godot_object_inspect", {"scene_id": other_document["scene_id"], "node_path": ".", "properties": ["position"]})["properties"] == other_before["properties"]
        client.call("godot_scene_undo", {"scene_id": main_document["scene_id"], "expected_revision": main_change["revision"]})
        client.call("godot_scene_open", {"path": "res://main.tscn"})
        report["inactive_scene_identity_and_isolation"] = True
        page_document = client.call("godot_scene_list")["active"]
        first_page = client.call("godot_scene_tree", {"scene_id": page_document["scene_id"], "limit": 1})
        assert first_page["has_more"] and len(first_page["nodes"]) == 1, first_page
        next_page = client.call("godot_scene_tree", {"scene_id": page_document["scene_id"], "limit": 1, "snapshot": first_page["snapshot"], "offset": first_page["next_offset"]})
        assert next_page["nodes"][0]["path"] != first_page["nodes"][0]["path"]
        page_mutation = client.call("godot_object_set_properties", {"scene_id": page_document["scene_id"], "expected_revision": page_document["revision"], "changes": {"health": 102}})
        invalid_page = client.call("godot_scene_tree", {"scene_id": page_document["scene_id"], "snapshot": first_page["snapshot"], "offset": 1}, allow_error=True)
        assert "SNAPSHOT_CHANGED" in json.dumps(invalid_page), invalid_page
        client.call("godot_scene_undo", {"scene_id": page_document["scene_id"], "expected_revision": page_mutation["revision"]})
        report["editor_tree_snapshot_pagination"] = True

        report["scene_document_lifecycle"] = True

        # Native menu/Inspector actions alternate with MCP edits in one real editor.
        def health():
            current = active_document()
            return client.call("godot_object_inspect", {"scene_id": current["scene_id"], "properties": ["health"]})["properties"][0]["value"]

        def human_action(action):
            edit("godot_object_set_properties", {"changes": {"human_probe": action}})
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                current = active_document()
                observed = client.call("godot_object_inspect", {"scene_id": current["scene_id"], "properties": ["human_probe_done"]})
                if observed["properties"][0]["value"] == action:
                    return
            raise AssertionError("Native editor action deadline")

        def probe(name):
            value = inspect_editor({"properties": [name]})["properties"][0]["value"]
            if isinstance(value, dict) and value.get("_type") == "Array":
                return value["values"]
            if isinstance(value, dict) and value.get("_type") == "Dictionary":
                return dict(value["entries"])
            return value

        def native_ui(command):
            edit("godot_object_set_properties", {"changes": {"native_ui_command": command}})
            deadline = time.monotonic() + 5
            while probe("native_ui_done") != command:
                assert time.monotonic() < deadline, "Native UI handler deadline"

        native_ui(1)
        original_health = health()
        human_action(1)
        assert health() == 117
        stale = active_document()
        undone = undo_document()
        assert health() == original_health
        assert undone["revision"] > stale["revision"]
        redo_document()
        assert health() == 117
        assert probe("native_inspector_events")[:3] == [117, original_health, 117], probe("native_inspector_events")
        report["inspector_ui_property_signal_and_undo_redo_shared_completion"] = True
        stale_write = client.call("godot_object_set_properties", {"scene_id": stale["scene_id"], "expected_revision": stale["revision"], "changes": {"health": 999}}, allow_error=True)
        assert "REVISION_CONFLICT" in json.dumps(stale_write), stale_write
        # Deferred native Undo must undo the setter action itself without corrupting
        # the manager stacks; native Redo then reapplies it.
        human_action(2)
        edit("godot_object_set_properties", {"changes": {"health": original_health, "human_probe": 0}})
        document = active_document()
        tree_ref = client.call("godot_object_call", {"scene_id": document["scene_id"], "method": "get_tree"})["value"]
        denied = edit("godot_object_set_properties", {"object_ref": tree_ref["ref"], "changes": {"paused": True}}, allow_error=True)
        assert "READ_ONLY_OBJECT" in json.dumps(denied), denied
        denied = edit("godot_object_call", {"method": "set_process", "arguments": [False], "allow_mutation": True}, allow_error=True)
        assert "MUTATION_NOT_DECLARED" in json.dumps(denied), denied
        denied = edit("godot_object_set_properties", {"changes": {"scene_file_path": "res://wrong.tscn"}}, allow_error=True)
        assert "PROPERTY_NOT_FOUND" in json.dumps(denied), denied
        restart_before = probe("native_restart_requests")
        edit("godot_object_set_properties", {"changes": {"health": 123}})
        assert probe("native_restart_requests") == restart_before + 1
        report["inspector_native_restart_feedback"] = True
        assert probe("native_inspector_events")[-1] == 123
        native_ui(2)
        assert health() == original_health and probe("native_wrong_inspector_events") == 0
        report["inspector_target_switch_does_not_misroute_undo_notifications"] = True
        report["native_undo_interleaving_monotonic_conflict_and_editor_object_guards"] = True
        # Native path rewriting and its inverse are in the very same Undo action.
        before_path = client.call("godot_object_inspect", {"scene_id": document["scene_id"], "properties": ["target_path"]})["properties"][0]["value"]
        edit_operation("rename", node_path="Marker", name="MovedMarker")
        after_path = client.call("godot_object_inspect", {"scene_id": active_document()["scene_id"], "properties": ["target_path"]})["properties"][0]["value"]
        assert after_path != before_path and "MovedMarker" in json.dumps(after_path), after_path
        undo_document()
        restored_path = client.call("godot_object_inspect", {"scene_id": active_document()["scene_id"], "properties": ["target_path"]})["properties"][0]["value"]
        assert restored_path == before_path, (before_path, restored_path)
        report["native_nodepath_rename_and_undo"] = True
        ambiguous = edit("godot_edit_batch", {"operations": [{"op": "rename", "node_path": "Marker", "name": "MustNotRename"}, {"op": "set", "node_path": ".", "changes": {"target_path": {"_type": "NodePath", "value": "Background"}}}]}, allow_error=True)
        assert "INVALID_EDIT" in json.dumps(ambiguous), ambiguous
        assert any(node["path"] == "Marker" for node in edited_tree()["nodes"])
        foreign_value = edit("godot_object_set_properties", {"changes": {"user_data": {"_type": "Dictionary", "entries": [["editor_tree", tree_ref]]}}}, allow_error=True)
        assert "INVALID_REFERENCE_SCOPE" in json.dumps(foreign_value), foreign_value
        report["ambiguous_reference_batches_and_editor_object_values_rejected"] = True

        def edit_tool(tool_name, **arguments):
            document = client.call("godot_scene_list")["active"]
            values = {"scene_id": document["scene_id"], "expected_revision": document["revision"], **arguments}
            if tool_name == "godot_edit_batch":
                return client.call("godot_tools_call", {"tool_name": tool_name, "arguments": values})
            return client.call(tool_name, values)

        operations = [
            {"op": "create", "name": "BatchParent", "type": "Node3D"},
            {"op": "create", "name": "Child", "type": "Node3D", "parent_path": "BatchParent"},
            {"op": "rename", "node_path": "BatchParent/Child", "name": "Renamed"},
            {"op": "instance_scene", "name": "Instance", "path": "res://created_scene.tscn"},
        ]
        preview = edit_tool("godot_edit_batch", operations=operations, dry_run=True)
        assert not preview["applied"]
        assert not any(n["name"] == "BatchParent" for n in edited_tree()["nodes"])
        edit_tool("godot_edit_batch", operations=operations)
        edit_tool("godot_scene_undo")
        assert not any(n["name"] == "BatchParent" for n in edited_tree()["nodes"])
        edit_tool("godot_scene_redo")
        edit_operation("duplicate", node_path="BatchParent", name="CopiedParent")
        edit_operation("reparent", node_path="BatchParent/Renamed", parent_path=".")
        edit_operation("groups", node_path="BatchParent", add=["automation_group"])
        edit_operation("delete", node_path="CopiedParent")
        edit_tool("godot_scene_undo")
        edit_operation("create", name="NativeCreated", type="Node3D")
        document = client.call("godot_scene_list")["active"]
        bad_batch = client.call("godot_edit_batch", {"scene_id": document["scene_id"], "expected_revision": document["revision"], "operations": [{"op": "create", "name": "MustNotExist"}, {"op": "delete", "node_path": "DoesNotExist"}]}, allow_error=True)
        assert bad_batch["isError"]
        assert not any(n["name"] == "MustNotExist" for n in edited_tree()["nodes"])
        save_document()
        close_document({"scene_path": "res://main.tscn"})
        open_document({"scene_path": "res://main.tscn"})
        paths = [n["path"] for n in edited_tree()["nodes"]]
        assert any(p.endswith("CopiedParent/Renamed") for p in paths), paths
        assert any(p.endswith("Instance/RenamedChild") for p in paths), paths
        assert any(p == "Renamed" for p in paths), paths
        compound_ops = [
            {"op": "create", "id": "sender", "name": "BatchSender", "type": "Node3D"},
            {"op": "attach", "node_path": "$sender", "script_path": "res://batch_script.gd"},
            {"op": "set", "node_path": "$sender", "changes": {"value": 9}},
            {"op": "set", "node_path": "$sender", "changes": {"value": 10}},
            {"op": "create", "id": "receiver", "name": "BatchReceiver", "type": "Node3D"},
            {"op": "attach", "node_path": "$receiver", "script_path": "res://batch_script.gd"},
            {"op": "connect", "node_path": "$sender", "signal_name": "marked", "receiver_path": "$receiver", "method": "receive_marker", "binds": [77]},
            {"op": "groups", "node_path": "$sender", "add": ["temporary_group"]},
            {"op": "groups", "node_path": "$sender", "remove": ["temporary_group"]},
            {"op": "set", "node_path": "$sender", "changes": {"position": {"_type": "Vector3", "x": 1, "y": 2, "z": 3}}},
        ]
        assert not edit_tool("godot_edit_batch", operations=compound_ops, dry_run=True)["applied"]
        compound = edit_tool("godot_edit_batch", operations=compound_ops)
        edit_operation("rename", node_path="BatchSender", name="RenamedSender")
        assert set(compound["references"]) == {"sender", "receiver"}, compound
        edit_tool("godot_scene_undo")
        edit_tool("godot_scene_undo")
        assert not any(n["name"] == "RenamedSender" for n in edited_tree()["nodes"])
        edit_tool("godot_scene_redo")
        edit_tool("godot_scene_redo")
        compound_values = inspect_editor({"node_path": "RenamedSender", "properties": ["value", "position"]})
        assert {p["name"]: p["value"] for p in compound_values["properties"]}["value"] == 10, compound_values
        save_document()
        close_document({"scene_path": "res://main.tscn"})
        open_document({"scene_path": "res://main.tscn"})
        connections = (Path(state["project_path"]) / "main.tscn").read_text()
        assert 'signal="marked"' in connections and 'binds= [77]' in connections, connections[-1000:]
        report["compound_attach_set_connect_temporary_refs"] = True
        report["structural_batch_and_persistence"] = True

        connection = dict(node_path="NativeCreated", signal_name="renamed", receiver_path=".", method="show")
        assert edit_operation("connect", **connection)["operations"][0]["connected"]
        assert not edit_operation("connect", **connection)["applied"]
        assert not edit_operation("disconnect", **connection)["operations"][0]["connected"]
        edit_tool("godot_scene_undo")
        save_document()
        # Disk edits use ordinary development tools; MCP only refreshes the active editor.
        (Path(state["project_path"]) / "patched_script.gd").write_text("extends Node\n\nfunc answer():\n\treturn 2\n", encoding="utf-8")
        assert client.call("godot_filesystem_scan")["scanned"]
        report["persistent_signals_and_external_script_edits"] = True

        assert client.call("godot_script_reload", {"paths": ["res://patched_script.gd"]})["scripts"][0]["reloaded"]
        reimported = client.call("godot_resource_reimport", {"paths": ["res://test_icon.svg"]})
        reimport_job = client.call("godot_job_wait", {"job_id": reimported["job_id"], "timeout_ms": 15000})
        assert reimport_job["state"] == "succeeded", reimport_job
        assert reimport_job["result"]["completed"] and reimport_job["result"]["success"], reimport_job
        failed_import = client.call("godot_resource_reimport", {"paths": ["res://missing_asset.svg"]})
        failed_import_done = client.call("godot_job_wait", {"job_id": failed_import["job_id"], "timeout_ms": 15000})
        assert failed_import_done["state"] == "failed" and "FILE_NOT_FOUND" in failed_import_done["error"], failed_import_done
        changed_job = client.call("godot_job_wait", {"job_id": failed_import["job_id"], "timeout_ms": 1000, "until": "changed", "after_sequence": 0})
        assert changed_job["sequence"] > 0
        cancelled_terminal = client.call("godot_job_cancel", {"job_id": failed_import["job_id"]})
        assert not cancelled_terminal["cancellation_accepted"] and cancelled_terminal["state"] == "failed"
        filtered_events = client.call("godot_events_read", {"job_id": failed_import["job_id"], "kinds": ["job_state"]})
        assert filtered_events["events"] and all(e["job_id"] == failed_import["job_id"] for e in filtered_events["events"])

        document = client.call("godot_scene_list")["active"]
        tree = client.call("godot_scene_tree", {"scene_id": document["scene_id"]})
        assert not tree["truncated"] and tree["nodes"][0]["path"] == "."
        query = client.call("godot_object_query", {"scene_id": document["scene_id"], "class_name": "Node3D", "name_pattern": "Batch*"})
        assert any("automation_group" in node["groups"] for node in query["nodes"])
        report["targeted_import_reload_and_queries"] = True

        animation = client.call("godot_resource_create", {"class_name": "Animation"})["resource"]
        library = client.call("godot_resource_create", {"class_name": "AnimationLibrary"})["resource"]
        track = edit_tool("godot_animation_edit", object_ref=animation["ref"], operation="add_track", track_type=0, path="Marker:position:x")["track"]
        edit_tool("godot_animation_edit", object_ref=animation["ref"], operation="set_key", track=track, time=0, value=0)
        edit_tool("godot_animation_edit", object_ref=animation["ref"], operation="set_key", track=track, time=1, value=10)
        edit_tool("godot_animation_edit", object_ref=animation["ref"], operation="set_metadata", length=2, loop_mode=1)
        edit_tool("godot_animation_edit", object_ref=animation["ref"], operation="remove_key", track=track, key=0)
        edit_tool("godot_scene_undo")
        edit_tool("godot_animation_edit", object_ref=animation["ref"], operation="remove_track", track=track)
        edit_tool("godot_scene_undo")
        edit_tool("godot_object_call", object_ref=library["ref"], method="add_animation", arguments=[{"_type": "StringName", "value": "move"}, animation], allow_mutation=True)
        edit_operation("create", name="AnimationPlayer", type="AnimationPlayer")
        edit_tool("godot_object_call", node_path="AnimationPlayer", method="add_animation_library", arguments=[{"_type": "StringName", "value": ""}, library], allow_mutation=True)
        document = client.call("godot_scene_list")["active"]
        animation_target = {"scene_id": document["scene_id"], "node_path": "AnimationPlayer", "animation_name": "move"}
        assert client.call("godot_animation_list", animation_target)["animations"][0]["name"] == "move"
        inspected_animation = client.call("godot_animation_inspect", animation_target)
        assert inspected_animation["tracks"][0]["key_count"] == 2, inspected_animation
        sampled_animation = client.call("godot_animation_sample", {**animation_target, "mode": "tracks", "times": [0.5]})
        assert sampled_animation["tracks"][0]["samples"][0]["value"] == 5.0, sampled_animation
        save_document()
        close_document({"scene_path": "res://main.tscn"})
        open_document({"scene_path": "res://main.tscn"})
        document = client.call("godot_scene_list")["active"]
        animation_target["scene_id"] = document["scene_id"]
        assert client.call("godot_animation_inspect", animation_target)["tracks"][0]["key_count"] == 2
        report["animation_edit_sample_undo_persistence"] = True
        edit_operation("rename", node_path="Marker", name="TrackMarker")
        assert client.call("godot_animation_inspect", animation_target)["tracks"][0]["path"] == "TrackMarker:position:x"
        undo_document()
        assert client.call("godot_animation_inspect", animation_target)["tracks"][0]["path"] == "Marker:position:x"
        edit_operation("create", name="ReferenceContainer", type="Node2D")
        edit_operation("reparent", node_path="Marker", parent_path="ReferenceContainer")
        assert client.call("godot_animation_inspect", animation_target)["tracks"][0]["path"] == "ReferenceContainer/Marker:position:x"
        undo_document()
        assert client.call("godot_animation_inspect", animation_target)["tracks"][0]["path"] == "Marker:position:x"
        undo_document()
        report["native_animation_track_rename_reparent_undo"] = True

        pose_doc = client.call("godot_scene_open", {"path": "res://pose_rig.tscn"})
        pose_target = {"scene_id": pose_doc["scene_id"], "node_path": "AnimationPlayer"}
        constructions_before = client.call("godot_object_inspect", {"scene_id": pose_doc["scene_id"], "node_path": ".", "properties": ["construction_total"]})["properties"][0]["value"]
        poses = client.call("godot_animation_sample", {**pose_target, "mode": "isolated", "animation_name": "lift", "times": [0, 0.5, 1], "skeleton_paths": ["Rig"]})
        assert [s["skeletons"][0]["bones"][0]["position"]["y"] for s in poses["samples"]] == [0, 1, 2], poses
        assert poses["world_isolated"] and not poses["project_scripts_copied"] and poses["ignored_tracks"], poses
        pose_current = client.call("godot_scene_list")["active"]
        assert client.call("godot_object_inspect", {"scene_id": pose_doc["scene_id"], "node_path": ".", "properties": ["construction_total"]})["properties"][0]["value"] == constructions_before
        actual_pose = client.call("godot_animation_sample", {**pose_target, "mode": "live", "skeleton_paths": ["Rig"]})
        assert actual_pose["samples"][0]["skeletons"][0]["bones"][0]["position"]["y"] == 0, actual_pose
        assert client.call("godot_object_inspect", {"scene_id": pose_doc["scene_id"], "node_path": ".", "properties": ["calls"]})["properties"][0]["value"] == 0
        client.call("godot_scene_open", {"path": "res://main.tscn"})
        report["isolated_skeleton_poses_without_script_or_method_effects"] = True


        settings_before = client.call("godot_project_settings_get", {"keys": ["application/config/name"]})
        denied_input_setting = client.call("godot_project_settings_set", {"changes": {"input/test_action": None}, "expected_revision": settings_before["settings_revision"]}, allow_error=True)
        assert "NATIVE_EDITOR_REQUIRED" in json.dumps(denied_input_setting), denied_input_setting
        report["native_specialized_settings_caches_protected"] = True
        original_name = settings_before["settings"]["application/config/name"]["value"]
        client.call("godot_project_settings_set", {"changes": {"application/config/name": "Automation settings probe"}, "expected_revision": settings_before["settings_revision"]})
        assert client.call("godot_project_settings_get", {"keys": ["application/config/name"]})["settings"]["application/config/name"]["value"] == "Automation settings probe"
        settings_after = client.call("godot_project_settings_get", {"keys": ["application/config/name"]})
        stale_settings = client.call("godot_project_settings_set", {"changes": {"application/config/name": "Must not apply"}, "expected_revision": settings_before["settings_revision"]}, allow_error=True)
        assert "REVISION_CONFLICT" in json.dumps(stale_settings), stale_settings
        disk_conflict = client.call("godot_project_settings_set", {"changes": {"application/config/name": original_name}, "expected_revision": settings_after["settings_revision"], "save": True, "expected_disk_hash": "invalid"}, allow_error=True)
        assert "DISK_CONFLICT" in json.dumps(disk_conflict), disk_conflict
        restored_settings = client.call("godot_project_settings_set", {"changes": {"application/config/name": original_name}, "expected_revision": settings_after["settings_revision"], "save": True, "expected_disk_hash": settings_after["disk_hash"]})
        assert restored_settings["saved"], restored_settings
        assert client.call("godot_editor_select", {"panel": "3d"})["selected_panel"] == "3d"
        document = client.call("godot_scene_list")["active"]
        assert client.call("godot_editor_select", {"scene_id": document["scene_id"], "node_paths": ["AnimationPlayer"]})["selected_nodes"] == 1
        if args.task_godot:
            compiled = subprocess.run([str(args.task_godot), "--headless", "--path", str(args.project), "--check-only", "--script", "res://validate_only.gd"], capture_output=True, text=True, timeout=15)
            assert compiled.returncode == 0 and "VALIDATION_MUST_NOT_EXECUTE" not in compiled.stdout + compiled.stderr, (compiled.stdout, compiled.stderr)
        report["live_project_settings_and_selection"] = True

        create_node({"name": "AutomationEdit", "type": "LineEdit", "properties": {"size": {"_type": "Vector2", "x": 180, "y": 32}}})
        save_document()

        set_editor({"node_path": ".", "property": "log_burst", "value": 2050})
        ring = read_logs({"max_entries": 2048})
        assert ring["dropped"] and len(ring["entries"]) == 2048
        first = read_logs({"cursor": ring["oldest_cursor"] - 1, "max_entries": 3, "levels": ["error"]})
        assert first["entries"] == [] and first["next_cursor"] == ring["oldest_cursor"] + 2
        assert first["has_more"]
        invalid_cursor = read_logs({"cursor": ring["latest_cursor"] + 100000}, allow_error=True)
        assert invalid_cursor["isError"]
        set_editor({"node_path": ".", "property": "log_burst", "value": 0})
        save_document()
        report["bounded_log_pagination"] = True

        def output(action="get", **arguments):
            return client.call("godot_output_control", {"action": action, **arguments})

        def emit_output():
            return edit("godot_object_set_properties", {"node_path": ".", "changes": {"output_probe": 1}})

        def read_output():
            first = output("read", max_chars=17)
            text = first["text"]
            page = first
            while page["has_more"]:
                page = output("read", offset=page["next_offset"], max_chars=1024, expected_revision=first["revision"], editor_epoch=first["editor_epoch"])
                text += page["text"]
            return text

        native_ui(1)
        original_output = output()
        try:
            output_history_before = active_document()["revision"]
            cleared = output("clear", editor_epoch=original_output["editor_epoch"])
            assert active_document()["revision"] == output_history_before
            assert cleared["message_count"] == 0 and cleared["cleared_panel"] and not cleared["cleared_diagnostics"], cleared
            before = read_logs({"max_entries": 2048})["latest_cursor"]
            emit_output()
            output_history_before = active_document()["revision"]
            configured = output("configure", visible=True, levels=["info"], search="MCP output", collapse=False)
            assert active_document()["revision"] == output_history_before
            assert configured["visible"] and configured["levels"] == ["info"] and not configured["collapse"], configured
            text = read_output()
            assert "MCP output marker Ω" in text and "MCP output rich" in text and "[b]" not in text, text
            assert text.count("MCP output duplicate") == 2 and "MCP output warning" not in text, text
            old_page = output("read", max_chars=5)
            collapsed = output("configure", collapse=True)
            collapsed_text = read_output()
            assert collapsed_text.count("MCP output duplicate") == 1, collapsed_text
            assert collapsed["counts"] == configured["counts"], (configured, collapsed)
            stale = client.call("godot_output_control", {"action": "read", "offset": 5, "expected_revision": old_page["revision"], "editor_epoch": old_page["editor_epoch"]}, allow_error=True)
            assert "OUTPUT_CHANGED" in json.dumps(stale), stale
            hidden = output("configure", visible=False)
            assert not hidden["visible"], hidden
            output("configure", visible=True, levels=["error", "warning"], search="fixture error")
            assert "MCP fixture error" in read_output()
            unfiltered = read_logs({"cursor": before, "max_entries": 2048})
            assert any("MCP output marker" in entry["message"] for entry in unfiltered["entries"]), unfiltered
            panel_cleared = output("clear", editor_epoch=original_output["editor_epoch"], scope="panel")
            assert read_output() == "" and panel_cleared["message_count"] == 0, panel_cleared
            retained = read_logs({"cursor": before, "max_entries": 2048})
            assert retained["history_generation"] == unfiltered["history_generation"] and any("MCP output marker" in entry["message"] for entry in retained["entries"]), retained
            cutoff = retained["latest_cursor"]
            emit_output()
            newer = read_logs({"cursor": cutoff, "max_entries": 2048})
            assert any("MCP output marker" in entry["message"] for entry in newer["entries"]), newer
            state_before_clear = output()
            selective = output("clear", editor_epoch=original_output["editor_epoch"], scope="diagnostics", through_cursor=cutoff)
            assert not selective["cleared_panel"] and selective["message_count"] == state_before_clear["message_count"], selective
            after = read_logs({"max_entries": 2048})
            assert after["history_generation"] == retained["history_generation"] + 1 and after["dropped"] and after["cleared_through_cursor"] == cutoff, after
            assert after["entries"] and all(entry["cursor"] > cutoff for entry in after["entries"]), after
            assert any("MCP output marker" in entry["message"] for entry in after["entries"]), after
            invalid_clear = client.call("godot_output_control", {"action": "clear", "editor_epoch": original_output["editor_epoch"], "scope": "all", "through_cursor": after["latest_cursor"] + 100000}, allow_error=True)
            assert "INVALID_CURSOR" in json.dumps(invalid_clear), invalid_clear
            wrong_epoch = client.call("godot_output_control", {"action": "clear", "editor_epoch": "old-editor"}, allow_error=True)
            assert "EDITOR_CONFLICT" in json.dumps(wrong_epoch), wrong_epoch
            both = output("clear", editor_epoch=original_output["editor_epoch"], scope="all", through_cursor=after["latest_cursor"])
            assert both["message_count"] == 0 and both["diagnostics"]["cleared_through_cursor"] == after["latest_cursor"], both
            assert both["diagnostics"]["retained_count"] == both["diagnostics"]["latest_cursor"] - after["latest_cursor"], both
            empty = read_logs({"cursor": after["latest_cursor"], "max_entries": 2048})
            assert all(entry["cursor"] > after["latest_cursor"] for entry in empty["entries"]) and empty["latest_cursor"] >= after["latest_cursor"], empty
            emit_output()
            appended = read_logs({"cursor": empty["latest_cursor"], "max_entries": 2048})
            assert appended["entries"] and all(entry["cursor"] > empty["latest_cursor"] for entry in appended["entries"]), appended
            events = probe("native_output_events")
            assert events["collapse"] > 0 and events["filters"] > 0 and events["search"] > 0, events
            widgets = probe("native_output_widgets")
            assert widgets["collapse"] == output()["collapse"] and widgets["search"] == output()["search"]
            native_ui(3)
            human_output = output()
            assert human_output["search"] == "human Output search" and human_output["levels"] == ["info"] and human_output["message_count"] == 0, human_output
            assert probe("native_output_events")["clear"] > 0
            output("configure", search="MCP output", collapse=False, levels=["info"])
            emit_output()
            assert "MCP output marker Ω" in read_output()
            report["output_native_controls_and_mcp_bidirectional_interleaving"] = True
            report["output_commands_do_not_pollute_scene_undo_history"] = True
            report["output_display_filters_search_collapse_and_pagination"] = True
            report["output_panel_clear_preserves_diagnostics"] = True
            report["output_history_cutoff_preserves_new_logs_and_cursor_identity"] = True
        finally:
            output("configure", **{key: original_output[key] for key in ["visible", "levels", "search", "collapse"]})
        save_document()

        reload_result = client.call("godot_filesystem_scan")
        assert reload_result["scanned"]
        report["project_reload"] = True
        log_cursor = read_logs({"max_entries": 2048})["latest_cursor"]

        main_run = finish_job(client.call("godot_run_start", {"scene": "main", "display_mode": "visible" if args.visible or args.require_capture else "headless_logic"}))
        session_id = main_run["session_id"]
        state = editor_state()
        if args.require_capture:
            client.call("godot_run_wait", {"run_id": main_run["run_id"], "stage": "first_render"})
            capture_result = client.request("tools/call", {"name": "godot_capture_frame", "arguments": {"session_id": session_id}})
            assert not capture_result["isError"], capture_result
            assert any(item["type"] == "image" for item in capture_result["content"])

        runtime_tree = runtime_tree_read({"max_depth": 8})
        paths = {node["path"] for node in runtime_tree["nodes"]}
        assert "/root/MCPTest" in paths
        assert "/root/MCPTest/Marker" in paths
        report["runtime_node_count"] = len(paths)

        inspected = inspect_runtime({"node_path": "/root/MCPTest", "properties": ["health", "input_seen"]})
        values = {item["name"]: item["value"] for item in inspected["properties"]}
        assert values["health"] == 100
        assert values["input_seen"] is False

        changed = set_runtime({"node_path": "/root/MCPTest", "property": "health", "value": 42})
        assert changed["applied"]

        send_action({"action": "test_action", "pressed": True})
        wait_frames({"frames": 2})
        inspected = inspect_runtime({"node_path": "/root/MCPTest", "properties": ["health", "input_seen"]})
        values = {item["name"]: item["value"] for item in inspected["properties"]}
        assert values == {"health": 42, "input_seen": True}
        send_action({"action": "test_action", "pressed": False})

        for button in [1, 2]:
            send_mouse({"kind": "button", "button": button, "pressed": True, "x": 80, "y": 70})
        send_mouse({"kind": "motion", "x": 92, "y": 65, "delta_x": 12, "delta_y": -5, "ctrl": True})
        send_mouse({"kind": "wheel", "direction": "up"})
        for button in [1, 2]:
            send_mouse({"kind": "button", "button": button, "pressed": False})
        mouse = inspect_runtime({
            "node_path": "/root/MCPTest", "properties": ["mouse_buttons_seen", "drag_total", "wheel_up_seen", "ctrl_seen"],
        })
        values = {item["name"]: item["value"] for item in mouse["properties"]}
        assert values["mouse_buttons_seen"] == 3
        assert values["drag_total"] == {"_type": "Vector2", "x": 12, "y": -5}
        assert values["wheel_up_seen"] and values["ctrl_seen"]
        report["raw_mouse_input"] = True

        sessions = client.call("godot_run_list")["sessions"]
        assert len(sessions) == 1
        session_id = sessions[0]["session_id"]
        runtime_target = {"context": "runtime", "session_id": session_id, "node_path": "/root/MCPTest"}
        typed = client.call("godot_object_inspect", {**runtime_target, "properties": ["health"]})
        assert typed["properties"][0]["value"] == 42
        client.call("godot_object_set_properties", {**runtime_target, "changes": {"health": 43}})
        reference = typed["object"]["ref"]
        assert client.call("godot_object_inspect", {"context": "runtime", "session_id": session_id, "object_ref": reference, "properties": ["health"]})["properties"][0]["value"] == 43
        explicit_tree = client.call("godot_scene_tree", {"context": "runtime", "session_id": session_id})
        assert any(n["path"] == "/root/MCPTest" for n in explicit_tree["nodes"])
        runtime_page = client.call("godot_scene_tree", {"context": "runtime", "session_id": session_id, "limit": 1})
        runtime_next_page = client.call("godot_scene_tree", {"context": "runtime", "session_id": session_id, "limit": 1, "snapshot": runtime_page["snapshot"], "offset": 1})
        assert runtime_page["has_more"] and runtime_next_page["nodes"][0]["path"] != runtime_page["nodes"][0]["path"]

        explicit_query = client.call("godot_object_query", {"context": "runtime", "session_id": session_id, "name_pattern": "Marker"})
        assert len(explicit_query["nodes"]) == 1
        invalid_session = client.call("godot_input_send", {"session_id": "stale-session", "kind": "key", "keycode": 65}, allow_error=True)
        assert invalid_session["isError"]
        client.call("godot_input_send", {"session_id": session_id, "kind": "key", "keycode": 65, "pressed": True})
        client.call("godot_input_send", {"session_id": session_id, "kind": "action_event", "action": "test_action", "pressed": True})
        client.call("godot_runtime_wait", {"session_id": session_id, "frames": 2, "clock": "physics"})
        input_state = client.call("godot_object_inspect", {**runtime_target, "properties": ["typed_key_seen", "typed_key_pressed", "action_event_seen"]})
        assert all(item["value"] for item in input_state["properties"]), input_state
        released = client.call("godot_input_release_all", {"session_id": session_id})
        assert released["released"] == 2
        client.call("godot_runtime_wait", {"session_id": session_id, "condition": "property", "node_path": "/root/MCPTest", "property": "typed_key_pressed", "value": False})
        input_state = client.call("godot_object_inspect", {**runtime_target, "properties": ["typed_key_pressed"]})
        assert not input_state["properties"][0]["value"]
        client.call("godot_object_release", {"context": "runtime", "session_id": session_id, "object_refs": [reference]})
        expired = client.call("godot_object_inspect", {"context": "runtime", "session_id": session_id, "object_ref": reference}, allow_error=True)
        assert expired["isError"]
        client.call("godot_object_call", {"context": "runtime", "session_id": session_id, "node_path": "/root/MCPTest/RenamedSender", "method": "trigger_marker", "allow_mutation": True})
        marker_result = client.call("godot_object_inspect", {"context": "runtime", "session_id": session_id, "node_path": "/root/MCPTest/BatchReceiver", "properties": ["captured"]})
        assert marker_result["properties"][0]["value"] == 87, marker_result
        report["explicit_runtime_sessions_and_keyboard"] = True

        runtime_animation = {"context": "runtime", "session_id": session_id, "node_path": "/root/MCPTest/AnimationPlayer", "animation_name": "move"}
        assert client.call("godot_animation_list", runtime_animation)["animations"][0]["name"] == "move"
        assert client.call("godot_animation_inspect", runtime_animation)["tracks"][0]["key_count"] == 2
        assert client.call("godot_animation_sample", {**runtime_animation, "mode": "tracks", "times": [0.5]})["tracks"][0]["samples"][0]["value"] == 5.0
        reflected = client.call("godot_object_call", {**runtime_target, "method": "get_class"})
        assert reflected["value"] == "Node2D", reflected

        assert client.call("godot_ui_focus", {"session_id": session_id, "node_path": "/root/MCPTest/AutomationEdit"})["focused"]
        client.call("godot_input_send", {"session_id": session_id, "kind": "text", "text": "Hello中文"})
        client.call("godot_runtime_wait", {"session_id": session_id, "frames": 2})
        controls = client.call("godot_ui_inspect", {"session_id": session_id, "node_path": "/root/MCPTest"})["controls"]
        assert any(c.get("text") == "Hello中文" and c["focused"] for c in controls), controls
        sequence = client.call("godot_input_sequence", {"session_id": session_id, "steps": [{"event": {"kind": "key", "keycode": 65, "pressed": True}}, {"after_frames": 2, "event": {"kind": "key", "keycode": 65, "pressed": False}}]})
        assert sequence["completed"] and sequence["released"]
        interrupted = client.call("godot_input_sequence", {"session_id": session_id, "timeout_ms": 1, "steps": [{"event": {"kind": "key", "keycode": 65, "pressed": True}}, {"after_frames": 120, "event": {"kind": "key", "keycode": 65, "pressed": False}}]}, allow_error=True)
        assert interrupted["isError"]
        client.call("godot_runtime_wait", {"session_id": session_id, "frames": 2})
        client.call("godot_runtime_wait", {"session_id": session_id, "condition": "property", "node_path": "/root/MCPTest", "property": "typed_key_pressed", "value": False})
        assert not client.call("godot_object_inspect", {**runtime_target, "properties": ["typed_key_pressed"]})["properties"][0]["value"]
        client.call("godot_input_send", {"session_id": session_id, "kind": "wheel", "direction": "up"})
        report["text_ui_focus_and_input_sequences"] = True

        for event in [{"kind": "touch", "index": 1, "pressed": True, "x": 20, "y": 20}, {"kind": "touch_motion", "index": 1, "x": 25, "y": 20, "delta_x": 5}, {"kind": "joy_button", "device": 0, "joy_button": 0, "pressed": True}, {"kind": "joy_motion", "device": 0, "axis": 0, "value": 0.75}]:
            client.call("godot_input_send", {"session_id": session_id, **event})
        client.call("godot_runtime_wait", {"session_id": session_id, "frames": 2})
        sampled = client.call("godot_object_inspect", {**runtime_target, "properties": ["touch_pressed", "touch_drag_seen", "joy_pressed", "joy_axis"]})
        sample_values = {p["name"]: p["value"] for p in sampled["properties"]}
        assert sample_values == {"touch_pressed": True, "touch_drag_seen": True, "joy_pressed": True, "joy_axis": 0.75}, sample_values
        client.call("godot_input_release_all", {"session_id": session_id})
        client.call("godot_runtime_wait", {"session_id": session_id, "frames": 2})
        released = client.call("godot_object_inspect", {**runtime_target, "properties": ["touch_pressed", "joy_pressed", "joy_axis"]})
        assert all(not p["value"] for p in released["properties"]), released
        report["touch_and_gamepad_input"] = True

        second_client = MCPClient(args.sidecar.resolve(), args.project, args.task_godot)
        try:
            client.call("godot_input_send", {"session_id": session_id, "kind": "action_state", "action": "test_action", "pressed": True})
            second_client.call("godot_input_send", {"session_id": session_id, "kind": "action_state", "action": "test_action", "pressed": True})
            client.call("godot_input_release_all", {"session_id": session_id})
            client.call("godot_runtime_wait", {"session_id": session_id, "frames": 2})
            held = client.call("godot_object_inspect", {**runtime_target, "properties": ["action_pressed_now"]})
            assert held["properties"][0]["value"]
        finally:
            second_client.close()
        client.call("godot_runtime_wait", {"session_id": session_id, "frames": 3})
        unheld = client.call("godot_object_inspect", {**runtime_target, "properties": ["action_pressed_now"]})
        assert not unheld["properties"][0]["value"]
        if args.task_godot:
            debug_uri = client.call("godot_run_list")["debugger_uri"]
            assert debug_uri.startswith("tcp://"), debug_uri
            extra = subprocess.Popen([str(args.task_godot), "--headless", "--path", state["project_path"], "--remote-debug", debug_uri], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                ready = client.call("godot_run_wait", {"minimum_sessions": 2, "timeout_ms": 9000})
                second_session = next(s["session_id"] for s in ready["sessions"] if s["pid"] == extra.pid)
                assert second_session != session_id
                second_target = {"context": "runtime", "session_id": second_session, "node_path": "/root/MCPTest"}
                client.call("godot_object_set_properties", {**second_target, "changes": {"health": 88}})
                assert client.call("godot_object_inspect", {**second_target, "properties": ["health"]})["properties"][0]["value"] == 88
                assert client.call("godot_object_inspect", {**runtime_target, "properties": ["health"]})["properties"][0]["value"] == 43
                client.call("godot_input_send", {"session_id": second_session, "kind": "key", "keycode": 66, "pressed": True})
                client.call("godot_runtime_wait", {"session_id": second_session, "frames": 2})
                assert client.call("godot_object_inspect", {**second_target, "properties": ["typed_key_pressed"]})["properties"][0]["value"]
                assert not client.call("godot_object_inspect", {**runtime_target, "properties": ["typed_key_pressed"]})["properties"][0]["value"]
                report["two_runtime_instances_isolated"] = True
            finally:
                extra.terminate()
                extra.wait(timeout=15)
        report["input_connection_ownership"] = True

        logs = read_logs({"cursor": log_cursor, "levels": ["error", "warning"], "max_entries": 2048})
        assert any(entry["message"] == "MCP fixture error" and entry["level"] == "error" and entry.get("source") == "runtime" for entry in logs["entries"]), logs
        assert any(entry["message"] == "MCP fixture warning" and entry["level"] == "warning" for entry in logs["entries"]), logs
        assert any(entry.get("stack") for entry in logs["entries"])
        assert read_logs({"cursor": logs["next_cursor"]})["entries"] == []
        filtered = client.call("godot_diagnostics_read", {"session_id": session_id, "source": "runtime", "max_entries": 2048})
        assert any(entry["message"] == "MCP fixture error" for entry in filtered["entries"]), filtered
        assert all(entry["runtime_pid"] == next(session["pid"] for session in client.call("godot_run_list")["sessions"] if session["session_id"] == session_id) for entry in filtered["entries"]), filtered
        report["runtime_diagnostics"] = True

        other_client = MCPClient(args.sidecar.resolve(), args.project, args.task_godot)
        try:
            assert other_client.call("godot_editor_status")["state"]["runtime_session_active"]
            assert editor_state()["runtime_session_active"]
        finally:
            other_client.close()
        # Reconnected observers must rediscover the editor without taking ownership of its game.
        observer = MCPClient(args.sidecar.resolve(), args.project, args.task_godot)
        try:
            assert observer.call("godot_editor_status")["state"]["runtime_session_active"]
        finally:
            observer.close()
        cli = subprocess.run(
            ["python3", str(args.sidecar), "--project", state["project_path"], "--call", "godot_editor_status"],
            capture_output=True, text=True, check=True, timeout=20,
        )
        assert json.loads(cli.stdout)["structuredContent"]["state"]["runtime_session_active"]
        report["multiple_clients_and_cli"] = True
        finish_job(client.call("godot_run_stop", {"run_id": main_run["run_id"]}))
        report["runtime_property_write"] = True
        report["input_action_seen"] = True

        if args.task_godot:
            def finish_job(value):
                done = client.call("godot_job_wait", {"job_id": value["job_id"], "timeout_ms": 15000})
                assert done["state"] == "succeeded", done
                return done["result"]
            edit("godot_object_set_properties", {"changes": {"human_profiler_auto": True}})
            client.call("godot_debugger_breakpoint_set", {"path": "res://ordinary_script.gd", "line": 3})
            native_points = inspect_editor({"properties": ["editor_breakpoints"]})["properties"][0]["value"]
            assert "ordinary_script.gd:3" in json.dumps(native_points), native_points
            human_action(3)
            native_points = inspect_editor({"properties": ["editor_breakpoints"]})["properties"][0]["value"]
            assert "ordinary_script.gd:3" not in json.dumps(native_points), native_points
            report["native_breakpoint_registry_human_removal"] = True
            owned = finish_job(client.call("godot_run_start", {"scene": "main"}))
            operation = client.call("godot_job_get", {"job_id": client.call("godot_editor_start", {"reuse": True})["job_id"]})
            operation_done = client.call("godot_job_wait", {"job_id": operation["job_id"], "timeout_ms": 15000})
            assert operation_done["state"] == "succeeded", operation_done
            operation_output = client.call("godot_artifact_read", {"artifact_id": operation_done["artifacts"][0]})
            assert json.loads(base64.b64decode(operation_output["data"]))["reused"]
            assert client.call("godot_diagnostics_read", {"job_id": operation["job_id"]})["state"] == "succeeded"
            assert any(event.get("job_id") == operation["job_id"] for event in client.call("godot_events_read")["events"])
            run_id = owned["run_id"]
            owned_session = owned["session_id"]
            blocked_live_edit = edit("godot_edit_batch", {"operations": [{"op": "create", "name": "MustNotLiveEdit"}]}, allow_error=True)
            assert "LIVE_EDIT_UNSUPPORTED" in json.dumps(blocked_live_edit), blocked_live_edit
            report["running_structural_edits_rejected_without_partial_mutation"] = True
            assert client.call("godot_run_wait", {"run_id": run_id, "stage": "first_physics"})["stage"] == "first_physics"
            render_rejected = client.call("godot_run_wait", {"run_id": run_id, "stage": "first_render"}, allow_error=True)
            assert "UNSUPPORTED_DISPLAY" in json.dumps(render_rejected), render_rejected
            paused = client.call("godot_debugger_control", {"session_id": owned_session, "action": "pause"})
            assert paused["paused"], paused
            frame = client.call("godot_debugger_state", {"session_id": owned_session})
            assert frame["paused"], frame
            client.call("godot_debugger_control", {"session_id": owned_session, "action": "continue"})
            probe_line = next(i for i, line in enumerate((Path(state["project_path"]) / "main.gd").read_text().splitlines(), 1) if "var probe_local :=" in line)
            client.call("godot_debugger_breakpoint_set", {"path": "res://main.gd", "line": probe_line})
            broken = client.call("godot_debugger_state", {"session_id": owned_session, "wait_paused": True, "frame": 0})
            assert broken["paused"] and any(f["function"] == "debug_probe" for f in broken["stack"]), broken
            client.call("godot_debugger_breakpoint_clear", {"path": "res://main.gd", "line": probe_line})
            for action in ["step_into", "step_over", "step_out"]:
                stepped = client.call("godot_debugger_control", {"session_id": owned_session, "action": action})
                assert stepped["paused"] and stepped["stack"], stepped
            client.call("godot_debugger_control", {"session_id": owned_session, "action": "continue"})
            runtime_original = client.call("godot_object_inspect", {"context": "runtime", "session_id": owned_session, "node_path": "/root/MCPTest", "properties": ["health"]})["properties"][0]["value"]
            pending_wait_id = client.send_request("tools/call", {"name": "godot_runtime_wait", "arguments": {"session_id": owned_session, "condition": "property", "node_path": "/root/MCPTest", "property": "health", "value": 2468, "timeout_ms": 8000}})
            concurrent_deadline = time.monotonic() + 4
            while editor_state()["pending_runtime_requests"] < 1:
                if time.monotonic() >= concurrent_deadline:
                    raise AssertionError("Runtime condition did not register while status requests were being served")
            client.call("godot_object_set_properties", {"context": "runtime", "session_id": owned_session, "node_path": "/root/MCPTest", "changes": {"health": 2468}})
            concurrent_wait_result = client.receive_request(pending_wait_id)
            assert not concurrent_wait_result["isError"] and concurrent_wait_result["structuredContent"]["satisfied"], concurrent_wait_result
            client.call("godot_object_set_properties", {"context": "runtime", "session_id": owned_session, "node_path": "/root/MCPTest", "changes": {"health": runtime_original}})
            report["same_client_concurrent_wait_status_and_mutation"] = True
            pulse_wait = client.call("godot_runtime_wait", {"session_id": owned_session, "condition": "signal", "node_path": "/root/MCPTest", "signal_name": "automation_pulse"})
            assert pulse_wait["satisfied"] and pulse_wait["condition"] == "signal", pulse_wait
            client.call("godot_object_call", {"context": "runtime", "session_id": owned_session, "node_path": "/root/MCPTest", "method": "create_ephemeral", "allow_mutation": True})
            expired_wait = client.call("godot_runtime_wait", {"session_id": owned_session, "condition": "signal", "node_path": "/root/MCPTest/WaitTarget", "signal_name": "renamed"}, allow_error=True)
            assert "OBJECT_EXPIRED" in json.dumps(expired_wait), expired_wait
            runtime_tree_object = client.call("godot_object_call", {"context": "runtime", "session_id": owned_session, "node_path": "/root/MCPTest", "method": "get_tree"})["value"]
            tree_target = {"context": "runtime", "session_id": owned_session, "object_ref": runtime_tree_object["ref"]}
            client.call("godot_object_set_properties", {**tree_target, "changes": {"paused": True}})
            try:
                paused_wait = client.call("godot_runtime_wait", {"session_id": owned_session, "frames": 1, "clock": "physics"}, allow_error=True)
                assert "PAUSED" in json.dumps(paused_wait), paused_wait
                paused_process = client.call("godot_runtime_wait", {"session_id": owned_session, "frames": 1, "clock": "process"})
                assert paused_process["paused"], paused_process
            finally:
                client.call("godot_object_set_properties", {**tree_target, "changes": {"paused": False}})
            report["signal_wait_object_expiry_and_pause_semantics"] = True
            property_wait = client.call("godot_runtime_wait", {"session_id": owned_session, "condition": "property", "node_path": "/root/MCPTest", "property": "health", "comparison": "gt", "value": 0})
            assert property_wait["satisfied"], property_wait
            signal_wait = client.call("godot_runtime_wait", {"session_id": owned_session, "condition": "signal", "node_path": "/root", "signal_name": "process_frame", "timeout_ms": 100}, allow_error=True)
            assert "SIGNAL_NOT_FOUND" in json.dumps(signal_wait), signal_wait
            unsatisfied = client.call("godot_runtime_wait", {"session_id": owned_session, "condition": "property", "node_path": "/root/MCPTest", "property": "health", "value": -999, "timeout_ms": 20}, allow_error=True)
            assert "TIMEOUT" in json.dumps(unsatisfied), unsatisfied
            monitors = client.call("godot_monitors_read", {"session_id": owned_session, "metrics": ["OBJECT_NODE_COUNT", "MEMORY_STATIC", "RENDER_TOTAL_DRAW_CALLS_IN_FRAME"]})
            assert monitors["metrics"][0]["value"] > 1 and monitors["metrics"][1]["unit"] == "bytes", monitors
            assert not monitors["metrics"][2]["available"], monitors
            profile = client.call("godot_profiler_capture", {"session_id": owned_session, "frames": 4})
            assert profile["complete"] and len(profile["frames"]) == 4 and profile["source"] == "engine_profiler", profile
            assert all("frame_seconds" in f for f in profile["frames"]), profile
            assert not profile["started_by_mcp"] and profile["profiler_state_after"]["enabled"], profile
            edit("godot_object_set_properties", {"changes": {"human_profiler_auto": False}})
            report["human_profiler_capture_preserved"] = True
            report["monitors_and_native_profiler"] = True
            pose_run = finish_job(client.call("godot_run_start", {"scene": "res://pose_rig.tscn"}))
            try:
                runtime_pose_target = {"context": "runtime", "session_id": pose_run["session_id"], "node_path": "/root/PoseRig/AnimationPlayer"}
                runtime_poses = client.call("godot_animation_sample", {**runtime_pose_target, "mode": "isolated", "animation_name": "lift", "times": [0.5], "skeleton_paths": ["Rig"]})
                assert runtime_poses["samples"][0]["skeletons"][0]["bones"][0]["position"]["y"] == 1, runtime_poses
                client.call("godot_object_call", {**runtime_pose_target, "method": "set_assigned_animation", "arguments": [{"_type": "StringName", "value": "lift"}], "allow_mutation": True})
                client.call("godot_object_call", {**runtime_pose_target, "method": "seek", "arguments": [0.5, True, True], "allow_mutation": True})
                live_pose = client.call("godot_animation_sample", {**runtime_pose_target, "mode": "live", "skeleton_paths": ["Rig"]})
                assert live_pose["samples"][0]["skeletons"][0]["bones"][0]["position"]["y"] == 1, live_pose
                assert not live_pose["playback_modified"]
                report["runtime_isolated_and_live_skeleton_poses"] = True
                disconnect_wait_id = client.send_request("tools/call", {"name": "godot_runtime_wait", "arguments": {"session_id": pose_run["session_id"], "frames": 120, "timeout_ms": 8000}})
                pending_deadline = time.monotonic() + 4
                while editor_state()["pending_runtime_requests"] < 1:
                    if time.monotonic() >= pending_deadline:
                        raise AssertionError("Runtime frame wait did not start")
                finish_job(client.call("godot_run_stop", {"run_id": pose_run["run_id"]}))
                disconnected_wait = client.receive_request(disconnect_wait_id)
                assert disconnected_wait["isError"] and "SESSION_DISCONNECTED" in json.dumps(disconnected_wait), disconnected_wait
                assert any(s["session_id"] == owned_session for s in client.call("godot_run_list")["sessions"])
                report["target_disconnect_fails_wait_without_affecting_other_run"] = True

            finally:
                finish_job(client.call("godot_run_stop", {"run_id": pose_run["run_id"]}))

            stopped = finish_job(client.call("godot_run_stop", {"run_id": run_id}))
            assert stopped["state"] == "exited" and stopped["exit_code"] == 0, stopped
            assert client.call("godot_run_wait", {"run_id": run_id, "stage": "exited"})["reached"]
            assert any(r["run_id"] == run_id for r in client.call("godot_run_list", {"include_stopped": True})["runs"])
            report["owned_runs_and_debugger_pause_step_variables"] = True
            terminated_client = MCPClient(args.sidecar.resolve(), args.project, args.task_godot)
            try:
                submitted = terminated_client.call("godot_run_start", {"display_mode": "headless_logic"})
                owned = terminated_client.call("godot_job_wait", {"job_id": submitted["job_id"], "timeout_ms": 15000})
                assert owned["state"] == "succeeded", owned
                owned_pid = owned["result"]["pid"]
                terminated_client.process.terminate()
                terminated_client.process.wait(timeout=15)
                try:
                    os.kill(owned_pid, 0)
                except ProcessLookupError:
                    pass
                else:
                    raise AssertionError("Terminated sidecar left its owned game process behind")
                report["sidecar_signal_reaps_owned_games"] = True
            finally:
                terminated_client.close()
            if args.hidden_gpu or args.visible:
                gpu = finish_job(client.call("godot_run_start", {"display_mode": "visible" if args.visible else "hidden_gpu", **({"rendering_method": "mobile", "rendering_driver": "metal"} if os.uname().sysname == "Darwin" else {})}))
                try:
                    gpu_session = gpu["session_id"]
                    client.call("godot_run_wait", {"run_id": gpu["run_id"], "stage": "first_render"})
                    capture_message = client.request("tools/call", {"name": "godot_capture_frame", "arguments": {"session_id": gpu_session}})
                    assert not capture_message["isError"], capture_message
                    capture = capture_message["structuredContent"]
                    assert "_mcp_image_base64" not in capture
                    assert any(item["type"] == "image" and base64.b64decode(item["data"]).startswith(b"\x89PNG") for item in capture_message["content"])
                    assert capture["width"] == 320 and capture["height"] == 180 and capture["window_visible"] == args.visible, capture
                    assert capture["center_rgba"][1] > 0.9 and capture["center_rgba"][0] < 0.1, capture
                    if os.uname().sysname == "Darwin":
                        assert capture["driver"] == "metal", capture
                    png = client.call("godot_artifact_read", {"artifact_id": capture["artifact_id"]})
                    assert png["mime_type"] == "image/png" and base64.b64decode(png["data"]).startswith(b"\x89PNG"), png
                    viewport = client.call("godot_capture_frame", {"session_id": gpu_session, "target": "viewport", "node_path": "/root/MCPTest/CaptureViewport"})
                    camera = client.call("godot_capture_frame", {"session_id": gpu_session, "target": "camera", "node_path": "/root/MCPTest/CaptureViewport/Camera2D"})
                    assert viewport["width"] == 64 and camera["width"] == 64, (viewport, camera)
                    visual = client.call("godot_profiler_capture", {"session_id": gpu_session, "kind": "visual", "frames": 3})
                    assert visual["complete"] and len(visual["frames"]) == 3, visual
                    report["gpu_driver"] = capture["driver"]
                    report["gpu_pixels_viewport_and_camera"] = True
                finally:
                    finish_job(client.call("godot_run_stop", {"run_id": gpu["run_id"]}))
            reused = client.call("godot_editor_start", {"reuse": True})
            reused_done = client.call("godot_job_wait", {"job_id": reused["job_id"], "timeout_ms": 15000})
            assert reused_done["state"] == "succeeded" and reused_done["result"]["reused"], reused_done
            current = editor_state()
            set_editor({"node_path": ".", "property": "health", "value": 99})
            stale_close = client.call("godot_editor_close", {"editor_epoch": current["editor_epoch"], "expected_document_revision": current["document_revision"]})
            stale_close_done = client.call("godot_job_wait", {"job_id": stale_close["job_id"], "timeout_ms": 15000})
            assert stale_close_done["state"] == "failed" and "DOCUMENT_CONFLICT" in stale_close_done["error"], stale_close_done
            current = editor_state()
            rejected = client.call("godot_editor_close", {"editor_epoch": current["editor_epoch"], "expected_document_revision": current["document_revision"]})
            rejected_done = client.call("godot_job_wait", {"job_id": rejected["job_id"], "timeout_ms": 15000})
            assert rejected_done["state"] == "failed" and "UNSAVED_CHANGES" in rejected_done["error"], rejected_done
            assert editor_state()["editor_epoch"] == current["editor_epoch"]
            restarted = client.call("godot_editor_restart", {"editor_epoch": current["editor_epoch"], "expected_document_revision": current["document_revision"], "unsaved": "save", "display_mode": "headless_logic"})
            restart_done = client.call("godot_job_wait", {"job_id": restarted["job_id"], "timeout_ms": 15000})
            assert restart_done["state"] == "succeeded", restart_done
            new_state = editor_state()
            assert new_state["editor_epoch"] != current["editor_epoch"]
            old_output = client.call("godot_output_control", {"action": "read", "offset": 1, "expected_revision": old_page["revision"], "editor_epoch": old_page["editor_epoch"]}, allow_error=True)
            assert "EDITOR_CONFLICT" in json.dumps(old_output), old_output
            assert new_state["edited_scene"] == "res://main.tscn", new_state
            assert "res://ordinary_script.gd" in restart_done["result"]["script_documents"]["restored"], restart_done
            assert "value := 43" in (Path(state["project_path"]) / "ordinary_script.gd").read_text()
            invalid_document = client.call("godot_script_patch", {"document_id": buffer["document_id"], "expected_hash": changed_buffer["hash"], "action": "undo"}, allow_error=True)
            assert "DOCUMENT_NOT_OPEN" in json.dumps(invalid_document), invalid_document

            closed = client.call("godot_editor_close", {"editor_epoch": new_state["editor_epoch"], "expected_document_revision": new_state["document_revision"]})
            closed_done = client.call("godot_job_wait", {"job_id": closed["job_id"], "timeout_ms": 15000})
            assert closed_done["state"] == "succeeded", closed_done
            assert not client.call("godot_editor_status")["connected"]
            started = client.call("godot_editor_start", {"display_mode": "headless_logic"})
            start_done = client.call("godot_job_wait", {"job_id": started["job_id"], "timeout_ms": 15000})
            assert start_done["state"] == "succeeded" and not start_done["result"]["reused"], start_done
            newest = editor_state()
            final_close = client.call("godot_editor_close", {"editor_epoch": newest["editor_epoch"], "expected_document_revision": newest["document_revision"]})
            final_close_done = client.call("godot_job_wait", {"job_id": final_close["job_id"], "timeout_ms": 15000})
            assert final_close_done["state"] == "succeeded", final_close_done
            report["editor_lifecycle_tasks"] = True
        report["verified_tools"] = sorted(client.verified_tools)
        assert tool_names <= client.verified_tools, sorted(tool_names - client.verified_tools)
        report["status"] = "passed"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    finally:
        client.close()


def run_isolated(args: argparse.Namespace) -> int:
    addon = args.sidecar.resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="godot-mcp-e2e-") as directory:
        project = Path(directory)
        shutil.copytree(addon, project / "addons/godot_mcp", ignore=shutil.ignore_patterns("__pycache__", "tests"))
        for fixture in (addon / "tests/fixtures").iterdir():
            if fixture.suffix != ".uid" and fixture.name != ".gdignore":
                shutil.copy2(fixture, project / fixture.name.removesuffix(".template"))
        project_settings = project / "project.godot"
        if not args.require_capture and not args.visible:
            with project_settings.open("a", encoding="utf-8") as settings_file:
                settings_file.write('\n[editor]\nrun/main_run_args="--headless"\n')
        prepared = subprocess.run([str(args.godot.resolve()), "--headless", "--path", str(project), "--script", "res://prepare_pose.gd"], capture_output=True, text=True, timeout=20)
        assert prepared.returncode == 0 and "SCRIPT ERROR" not in prepared.stderr, (prepared.stdout, prepared.stderr)
        # A fresh import ensures global scripted node classes are available before plugin loading.
        imported = subprocess.run(
            [str(args.godot.resolve()), "--headless", "--editor", "--path", str(project), "--quit"],
            capture_output=True, text=True, timeout=60,
        )
        if imported.returncode or "SCRIPT ERROR" in imported.stderr:
            raise AssertionError(f"Fixture import failed:\n{imported.stdout}\n{imported.stderr}")
        settings = project / ".godot/editor/project_metadata.cfg"
        settings.write_text('[game_view]\nembed_on_play=false\nmake_floating_on_play=false\n', encoding="utf-8")
        (project / ".godot/mcp").mkdir(parents=True, exist_ok=True)
        import socket
        with socket.socket() as stale_listener:
            stale_listener.bind(("127.0.0.1", 0))
            stale_port = stale_listener.getsockname()[1]
        (project / ".godot/mcp/endpoint.json").write_text(json.dumps({"version": 1, "host": "127.0.0.1", "port": stale_port, "pid": 99999999, "project_path": str(project), "token": "stale-fixture-token"}))
        log_path = project / "editor.log"
        with log_path.open("w") as log:
            editor = subprocess.Popen(
                [str(args.godot.resolve()), *([] if args.require_capture or args.visible else ["--headless"]),
                 "--verbose", "--editor", "--path", str(project), "res://main.tscn"],
                stdout=log, stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + 30
                endpoint_path = project / ".godot/mcp/endpoint.json"
                # macOS vnode notifications wait for directory changes, without polling sleeps.
                fd = os.open(project / ".godot/mcp", os.O_RDONLY)
                try:
                    with contextlib.closing(select_kqueue(fd)) as queue:
                        def endpoint_matches():
                            try:
                                return json.loads(endpoint_path.read_text()).get("pid") == editor.pid
                            except (OSError, ValueError):
                                return False
                        while not endpoint_matches():
                            if editor.poll() is not None:
                                raise AssertionError(log_path.read_text())
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                raise TimeoutError(log_path.read_text())
                            queue.control(None, 1, min(remaining, 1))
                finally:
                    os.close(fd)
                command = ["python3", str(Path(__file__).resolve()), "--sidecar", str(args.sidecar.resolve()), "--project", str(project), "--task-godot", str(args.godot.resolve())]
                if args.require_capture:
                    command.append("--require-capture")
                if args.hidden_gpu:
                    command.append("--hidden-gpu")
                if args.visible:
                    command.append("--visible")
                tested = subprocess.run(command, capture_output=True, text=True, timeout=120)
                print(tested.stdout, end="")
                if tested.returncode:
                    raise AssertionError(f"{tested.stderr}\nEditor log:\n{log_path.read_text()[-65536:]}")
                text = log_path.read_text()
                assert "SCRIPT ERROR" not in text and "Parse Error" not in text, text
                unexpected_errors = [line for line in text.splitlines() if "ERROR:" in line and "MCP fixture error" not in line]
                lines = text.splitlines()
                contexts = ["\n".join(lines[max(0, index - 2):index + 10]) for index, line in enumerate(lines) if "ERROR:" in line and "MCP fixture error" not in line]
                assert not unexpected_errors, contexts
                return 0
            finally:
                cleanup_fixture(project, args.godot.resolve())
                editor.wait(timeout=15)


def select_kqueue(fd: int):
    import select
    queue = select.kqueue()
    queue.control([select.kevent(fd, filter=select.KQ_FILTER_VNODE,
                                 flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR,
                                 fflags=select.KQ_NOTE_WRITE)], 0, 0)
    return queue


if __name__ == "__main__":
    raise SystemExit(main())
