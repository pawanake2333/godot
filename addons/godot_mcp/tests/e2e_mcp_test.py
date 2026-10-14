#!/usr/bin/env python3
from __future__ import annotations

import argparse
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


class MCPClient:
    def __init__(self, sidecar: Path, project: Path | None = None):
        self.process = subprocess.Popen(
            ["python3", str(sidecar), *(["--project", str(project)] if project else [])],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.next_id = 1

    def close(self) -> None:
        if self.process.stdin:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=3)

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        assert self.process.stdin and self.process.stdout
        self.process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=20):
                raise TimeoutError(f"MCP request timed out: {method}")
        line = self.process.stdout.readline()
        if not line:
            stderr = self.process.stderr.read() if self.process.stderr else ""
            raise RuntimeError(f"MCP sidecar exited without a response: {stderr}")
        response = json.loads(line)
        if response.get("id") != request_id:
            raise AssertionError(f"Response id mismatch: {response}")
        if "error" in response:
            raise AssertionError(f"MCP JSON-RPC error: {response['error']}")
        return response["result"]

    def call(self, name: str, arguments: dict[str, Any] | None = None, allow_error: bool = False) -> Any:
        result = self.request("tools/call", {"name": name, "arguments": arguments or {}})
        if result.get("isError") and not allow_error:
            raise AssertionError(f"Tool {name} failed: {result['content']}")
        return result.get("structuredContent", result)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sidecar", type=Path, required=True)
    parser.add_argument("--require-capture", action="store_true")
    parser.add_argument("--project", type=Path)
    parser.add_argument("--godot", type=Path, help="Launch an isolated fixture editor and clean it up after testing.")
    args = parser.parse_args()

    if args.godot:
        return run_isolated(args)
    client = MCPClient(args.sidecar.resolve(), args.project)
    report: dict[str, Any] = {}
    try:
        initialized = client.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "e2e-test", "version": "1"}})
        assert initialized["serverInfo"]["name"] == "godot-mcp"

        tools = client.request("tools/list")["tools"]
        tool_names = {tool["name"] for tool in tools}
        required = {
            "godot_get_state",
            "godot_get_open_scenes",
            "godot_close_scene",
            "godot_open_scene",
            "godot_list_commands",
            "godot_play",
            "godot_stop",
            "godot_get_runtime_tree",
            "godot_inspect_runtime_object",
            "godot_set_runtime_property",
            "godot_send_input_action",
        }
        assert required <= tool_names
        report["tool_count"] = len(tools)

        state = client.call("godot_get_state")
        assert "4.7.2-stable" in state["godot_version"]
        report["project_path"] = state["project_path"]

        scenes = client.call("godot_get_open_scenes")
        assert "res://main.tscn" in scenes["open_scenes"]
        assert scenes["edited_scene"] == "res://main.tscn"
        closed = client.call("godot_close_scene", {"scene_path": "res://main.tscn"})
        assert closed == {"closed": True, "scene_path": "res://main.tscn"}
        scenes = client.call("godot_get_open_scenes")
        assert "res://main.tscn" not in scenes["open_scenes"]
        opened = client.call("godot_open_scene", {"scene_path": "res://main.tscn"})
        assert opened == {"opened": True, "scene_path": "res://main.tscn"}
        report["scene_file_coordination"] = True

        commands = client.call("godot_list_commands")["commands"]
        assert "editor/editor_3d" in commands
        report["allowed_command_count"] = len(commands)

        executed = client.call("godot_run_command", {"key": "editor/editor_3d"})
        assert executed["executed"] == "editor/editor_3d"

        edited = client.call("godot_get_edited_scene", {"max_depth": 4})
        root_node = next(node for node in edited["nodes"] if node["name"] == "MCPTest")
        editor_path = root_node["path"]
        inspected_editor = client.call("godot_inspect_editor_node", {"node_path": editor_path, "properties": ["health"]})
        editor_values = {item["name"]: item["value"] for item in inspected_editor["properties"]}
        assert editor_values["health"] == 100
        changed_editor = client.call("godot_set_editor_property", {"node_path": editor_path, "property": "health", "value": 77})
        assert changed_editor["value"] == 77
        unsafe_close = client.call("godot_close_scene", {"scene_path": "res://main.tscn"}, allow_error=True)
        assert unsafe_close["isError"] is True
        report["unsaved_scene_protected"] = True
        client.call("godot_set_editor_property", {"node_path": editor_path, "property": "health", "value": 100})
        report["editor_property_write"] = True
        client.call("godot_save_scene")

        created = client.call("godot_create_editor_node", {
            "name": "SpawnPoint", "script_path": "res://spawn_point.gd",
            "properties": {"archetype_id": 17, "spawn_count": 3},
        })
        assert created["class"] == "Node3D"
        client.call("godot_create_editor_node", {"name": "Child", "parent_path": "SpawnPoint", "type": "Node3D"})
        duplicate = client.call("godot_create_editor_node", {"name": "SpawnPoint"}, allow_error=True)
        assert duplicate["isError"]
        client.call("godot_delete_editor_node", {"node_path": "SpawnPoint"})
        client.call("godot_editor_undo")
        restored = client.call("godot_get_edited_scene")["nodes"]
        assert any(node["name"] == "Child" for node in restored)
        client.call("godot_editor_redo")
        assert not any(node["name"] == "SpawnPoint" for node in client.call("godot_get_edited_scene")["nodes"])
        client.call("godot_editor_undo")
        client.call("godot_save_scene")
        client.call("godot_close_scene", {"scene_path": "res://main.tscn"})
        client.call("godot_open_scene", {"scene_path": "res://main.tscn"})
        saved = client.call("godot_inspect_editor_node", {"node_path": "SpawnPoint", "properties": ["archetype_id", "spawn_count"]})
        assert {item["name"]: item["value"] for item in saved["properties"]} == {"archetype_id": 17, "spawn_count": 3}
        assert any(node["name"] == "Child" for node in client.call("godot_get_edited_scene")["nodes"])
        root_delete = client.call("godot_delete_editor_node", {"node_path": "."}, allow_error=True)
        assert root_delete["isError"]
        escaped = client.call("godot_inspect_editor_node", {"node_path": "/root"}, allow_error=True)
        assert escaped["isError"]
        report["node_edit_undo_redo_persistence"] = True

        client.call("godot_set_editor_property", {"node_path": ".", "property": "log_burst", "value": 2050})
        ring = client.call("godot_get_logs", {"max_entries": 2048})
        assert ring["dropped"] and len(ring["entries"]) == 2048
        first = client.call("godot_get_logs", {"cursor": ring["oldest_cursor"] - 1, "max_entries": 3, "levels": ["error"]})
        assert first["entries"] == [] and first["next_cursor"] == ring["oldest_cursor"] + 2
        assert first["has_more"]
        invalid_cursor = client.call("godot_get_logs", {"cursor": ring["latest_cursor"] + 100000}, allow_error=True)
        assert invalid_cursor["isError"]
        client.call("godot_set_editor_property", {"node_path": ".", "property": "log_burst", "value": 0})
        client.call("godot_save_scene")
        report["bounded_log_pagination"] = True

        reload_result = client.call("godot_reload_project")
        assert reload_result["reloaded"]
        report["project_reload"] = True
        log_cursor = client.call("godot_get_logs", {"max_entries": 2048})["latest_cursor"]

        if not args.require_capture:
            capture_error = client.call("godot_capture", allow_error=True)
            assert capture_error["isError"] is True
            report["headless_capture_error"] = True

        client.call("godot_play", {"mode": "main"})
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            state = client.call("godot_get_state")
            if state["playing"] and state["runtime_session_active"]:
                break
        else:
            raise AssertionError(f"Runtime session did not become active: {state}")

        if args.require_capture:
            client.call("godot_wait_runtime_frames", {"frames": 3})
            capture_deadline = time.monotonic() + 15
            while True:
                capture_result = client.request("tools/call", {"name": "godot_capture", "arguments": {}})
                if not capture_result["isError"]:
                    break
                if time.monotonic() >= capture_deadline:
                    raise AssertionError(f"Game capture remained unavailable: {capture_result}")
                client.call("godot_wait_runtime_frames", {"frames": 1})
            image_content = next(item for item in capture_result["content"] if item["type"] == "image")
            image_bytes = __import__("base64").b64decode(image_content["data"])
            assert image_bytes.startswith(b"\x89PNG\r\n\x1a\n")
            assert __import__("struct").unpack(">II", image_bytes[16:24]) == (320, 180)
            assert not Path(capture_result["structuredContent"]["path"]).exists()
            report["capture_png_bytes"] = len(image_bytes)

        runtime_tree = client.call("godot_get_runtime_tree", {"max_depth": 8})
        paths = {node["path"] for node in runtime_tree["nodes"]}
        assert "/root/MCPTest" in paths
        assert "/root/MCPTest/Marker" in paths
        report["runtime_node_count"] = len(paths)

        inspected = client.call("godot_inspect_runtime_object", {"node_path": "/root/MCPTest", "properties": ["health", "input_seen"]})
        values = {item["name"]: item["value"] for item in inspected["properties"]}
        assert values["health"] == 100
        assert values["input_seen"] is False

        changed = client.call("godot_set_runtime_property", {"node_path": "/root/MCPTest", "property": "health", "value": 42})
        assert changed["value"] == 42

        client.call("godot_send_input_action", {"action": "test_action", "pressed": True})
        client.call("godot_wait_runtime_frames", {"frames": 2})
        inspected = client.call("godot_inspect_runtime_object", {"node_path": "/root/MCPTest", "properties": ["health", "input_seen"]})
        values = {item["name"]: item["value"] for item in inspected["properties"]}
        assert values == {"health": 42, "input_seen": True}
        client.call("godot_send_input_action", {"action": "test_action", "pressed": False})

        for button in [1, 2]:
            client.call("godot_send_mouse_event", {"kind": "button", "button": button, "pressed": True, "x": 80, "y": 70})
        client.call("godot_send_mouse_event", {"kind": "motion", "x": 92, "y": 65, "delta_x": 12, "delta_y": -5, "ctrl": True})
        client.call("godot_send_mouse_event", {"kind": "wheel", "direction": "up"})
        for button in [1, 2]:
            client.call("godot_send_mouse_event", {"kind": "button", "button": button, "pressed": False})
        mouse = client.call("godot_inspect_runtime_object", {
            "node_path": "/root/MCPTest", "properties": ["mouse_buttons_seen", "drag_total", "wheel_up_seen", "ctrl_seen"],
        })
        values = {item["name"]: item["value"] for item in mouse["properties"]}
        assert values["mouse_buttons_seen"] == 3
        assert values["drag_total"] == {"_type": "Vector2", "x": 12, "y": -5}
        assert values["wheel_up_seen"] and values["ctrl_seen"]
        report["raw_mouse_input"] = True

        logs = client.call("godot_get_logs", {"cursor": log_cursor, "levels": ["error", "warning"], "max_entries": 2048})
        assert any(entry["message"] == "MCP fixture error" and entry["level"] == "error" and entry.get("source") == "runtime" for entry in logs["entries"]), logs
        assert any(entry["message"] == "MCP fixture warning" and entry["level"] == "warning" for entry in logs["entries"]), logs
        assert any(entry.get("stack") for entry in logs["entries"])
        assert client.call("godot_get_logs", {"cursor": logs["next_cursor"]})["entries"] == []
        report["runtime_diagnostics"] = True

        other_client = MCPClient(args.sidecar.resolve(), args.project)
        try:
            assert other_client.call("godot_get_state")["playing"]
            assert client.call("godot_get_state")["playing"]
        finally:
            other_client.close()
        # A disconnected transport must rediscover and verify the editor before the next request.
        client.close()
        client = MCPClient(args.sidecar.resolve(), args.project)
        assert client.call("godot_get_state")["playing"]
        cli = subprocess.run(
            ["python3", str(args.sidecar), "--project", state["project_path"], "--call", "godot_get_state"],
            capture_output=True, text=True, check=True, timeout=20,
        )
        assert json.loads(cli.stdout)["structuredContent"]["playing"]
        report["multiple_clients_and_cli"] = True
        client.call("godot_stop")
        report["runtime_property_write"] = True
        report["input_action_seen"] = True
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
        # A fresh import ensures global scripted node classes are available before plugin loading.
        imported = subprocess.run(
            [str(args.godot.resolve()), "--headless", "--editor", "--path", str(project), "--quit"],
            capture_output=True, text=True, timeout=60,
        )
        if imported.returncode or "SCRIPT ERROR" in imported.stderr:
            raise AssertionError(f"Fixture import failed:\n{imported.stdout}\n{imported.stderr}")
        settings = project / ".godot/editor/project_metadata.cfg"
        settings.write_text('[game_view]\nembed_on_play=false\nmake_floating_on_play=false\n', encoding="utf-8")
        log_path = project / "editor.log"
        with log_path.open("w") as log:
            editor = subprocess.Popen(
                [str(args.godot.resolve()), *([] if args.require_capture else ["--headless"]),
                 "--editor", "--path", str(project), "res://main.tscn"],
                stdout=log, stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + 30
                endpoint_path = project / ".godot/mcp/endpoint.json"
                # macOS vnode notifications wait for directory changes, without polling sleeps.
                fd = os.open(project / ".godot/mcp", os.O_RDONLY)
                try:
                    with contextlib.closing(select_kqueue(fd)) as queue:
                        while not endpoint_path.exists():
                            if editor.poll() is not None:
                                raise AssertionError(log_path.read_text())
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                raise TimeoutError(log_path.read_text())
                            queue.control(None, 1, min(remaining, 1))
                finally:
                    os.close(fd)
                command = ["python3", str(Path(__file__).resolve()), "--sidecar", str(args.sidecar.resolve()), "--project", str(project)]
                if args.require_capture:
                    command.append("--require-capture")
                tested = subprocess.run(command, capture_output=True, text=True, timeout=120)
                print(tested.stdout, end="")
                if tested.returncode:
                    raise AssertionError(f"{tested.stderr}\nEditor log:\n{log_path.read_text()}")
                text = log_path.read_text()
                assert "SCRIPT ERROR" not in text and "Parse Error" not in text, text
                unexpected_errors = [line for line in text.splitlines() if "ERROR:" in line and "MCP fixture error" not in line]
                assert not unexpected_errors, unexpected_errors
                return 0
            finally:
                editor.terminate()
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
