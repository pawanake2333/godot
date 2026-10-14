#!/usr/bin/env python3
"""Dependency-free MCP stdio server for the Godot MCP editor addon."""

from __future__ import annotations

import argparse
import base64
import json
import socket
import sys
from pathlib import Path
from typing import Any

SERVER_INFO = {"name": "godot-mcp", "version": "0.2.0"}
SUPPORTED_PROTOCOLS = {"2024-11-05", "2025-03-26", "2025-06-18"}

TOOLS = [
    {
        "name": "godot_get_state",
        "description": "Get the current Godot editor, project, scene, and play state.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "godot_get_open_scenes",
        "description": "List open and unsaved editor scenes and identify the active scene.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "godot_close_scene",
        "description": "Close an editor scene by path before modifying its file. Unsaved scenes require force=true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "scene_path": {"type": "string"},
                "force": {"type": "boolean", "default": False},
            },
            "required": ["scene_path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "godot_open_scene",
        "description": "Open an editor scene by path, or activate its tab if it is already open.",
        "inputSchema": {
            "type": "object",
            "properties": {"scene_path": {"type": "string"}},
            "required": ["scene_path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "godot_list_commands",
        "description": "List editor command keys allowed by the addon's fixed safety policy.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "godot_run_command",
        "description": "Execute one allowlisted Godot editor command by stable key.",
        "inputSchema": {
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
            "additionalProperties": False,
        },
    },
    {
        "name": "godot_play",
        "description": "Run the main, current, or explicitly specified scene.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["main", "current", "custom"], "default": "main"},
                "scene_path": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "godot_stop",
        "description": "Stop the game currently launched by the editor.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "godot_capture",
        "description": "Capture the running game, including embedded, floating, or separate windows, and return a PNG image.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "godot_get_edited_scene",
        "description": "Get a bounded tree snapshot of the scene currently edited in Godot.",
        "inputSchema": {
            "type": "object",
            "properties": {"max_depth": {"type": "integer", "minimum": 0, "maximum": 64, "default": 16}},
            "additionalProperties": False,
        },
    },
    {
        "name": "godot_inspect_editor_node",
        "description": "Read selected properties from a node in the edited scene.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_path": {"type": "string"},
                "properties": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["node_path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "godot_set_editor_property",
        "description": "Set an edited scene node property through Godot Undo/Redo.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_path": {"type": "string"},
                "property": {"type": "string"},
                "value": {},
            },
            "required": ["node_path", "property", "value"],
            "additionalProperties": False,
        },
    },
    {
        "name": "godot_save_scene",
        "description": "Save the currently edited scene.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "godot_get_runtime_tree",
        "description": "Get a bounded scene tree snapshot from the running debug game.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "root_path": {"type": "string"},
                "max_depth": {"type": "integer", "minimum": 0, "maximum": 64, "default": 16},
                "max_nodes": {"type": "integer", "minimum": 1, "maximum": 4096, "default": 1024},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "godot_inspect_runtime_object",
        "description": "Inspect properties of a running object by node path or instance ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_path": {"type": "string"},
                "instance_id": {"type": "integer"},
                "properties": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "godot_set_runtime_property",
        "description": "Set a property on a running object by node path or instance ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_path": {"type": "string"},
                "instance_id": {"type": "integer"},
                "property": {"type": "string"},
                "value": {},
            },
            "required": ["property", "value"],
            "additionalProperties": False,
        },
    },
    {
        "name": "godot_send_input_action",
        "description": "Press or release an InputMap action in the running debug game.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "pressed": {"type": "boolean", "default": True},
                "strength": {"type": "number", "minimum": 0, "maximum": 1, "default": 1},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    },
]

TOOLS.extend([
    {
        "name": "godot_create_editor_node",
        "description": "Create a native or scripted child node in the edited scene through Undo/Redo.",
        "inputSchema": {
            "type": "object", "required": ["name"],
            "properties": {
                "parent_path": {"type": "string", "default": "."},
                "name": {"type": "string", "minLength": 1},
                "type": {"type": "string", "default": "Node"},
                "script_path": {"type": "string"},
                "properties": {"type": "object"},
            }, "additionalProperties": False,
        },
    },
    {
        "name": "godot_delete_editor_node",
        "description": "Delete an owned edited-scene node through Undo/Redo; preserves subtree owners on undo.",
        "inputSchema": {
            "type": "object", "required": ["node_path"],
            "properties": {"node_path": {"type": "string", "minLength": 1}},
            "additionalProperties": False,
        },
    },
    *[
        {"name": name, "description": description,
         "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}
        for name, description in [
            ("godot_editor_undo", "Undo the latest action in the edited scene's history."),
            ("godot_editor_redo", "Redo the latest action in the edited scene's history."),
            ("godot_reload_project", "Wait for a project filesystem scan, then reload open scripts using editor conflict handling."),
        ]
    ],
    {
        "name": "godot_get_logs",
        "description": "Read bounded editor Output and debug-game diagnostics after a cursor; includes runtime source and stack.",
        "inputSchema": {
            "type": "object", "properties": {
                "cursor": {"type": "integer", "minimum": 0, "default": 0},
                "max_entries": {"type": "integer", "minimum": 1, "maximum": 2048, "default": 200},
                "levels": {"type": "array", "items": {"type": "string", "enum": ["info", "editor", "warning", "error"]}},
            }, "additionalProperties": False,
        },
    },
    {
        "name": "godot_send_mouse_event",
        "description": "Inject raw viewport mouse button, relative motion, or wheel events into the debug game and wait one frame.",
        "inputSchema": {
            "type": "object", "required": ["kind"], "properties": {
                "kind": {"type": "string", "enum": ["button", "motion", "wheel"]},
                "button": {"type": "integer", "enum": [1, 2, 3, 8, 9], "default": 1},
                "pressed": {"type": "boolean", "default": True},
                "double_click": {"type": "boolean", "default": False},
                "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                "factor": {"type": "number", "minimum": 0.01, "maximum": 100, "default": 1},
                **{key: {"type": "number"} for key in ["x", "y", "delta_x", "delta_y"]},
                **{key: {"type": "boolean", "default": False} for key in ["shift", "ctrl", "alt", "meta"]},
            }, "additionalProperties": False,
        },
    },
    {
        "name": "godot_wait_runtime_frames",
        "description": "Wait for debug-game process-frame notifications, for input and rendering synchronization.",
        "inputSchema": {
            "type": "object", "properties": {"frames": {"type": "integer", "minimum": 1, "maximum": 120, "default": 1}},
            "additionalProperties": False,
        },
    },
])


class GodotBridge:
    def __init__(self, host: str, port: int | None, connect_timeout: float, request_timeout: float,
                 project: Path | None = None):
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self.request_timeout = request_timeout
        self.sock: socket.socket | None = None
        self.stream = None
        self.next_id = 1
        self.project = project.resolve() if project else None
        self.token = ""

    def close(self) -> None:
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def _connect(self) -> None:
        if self.sock is not None:
            return
        if self.project:
            endpoint_path = self.project / ".godot/mcp/endpoint.json"
            try:
                endpoint = json.loads(endpoint_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                raise ConnectionError(f"Godot MCP endpoint unavailable: {endpoint_path}. Start the custom editor with this project's plugin enabled.") from error
            if Path(endpoint.get("project_path", "")).resolve() != self.project:
                raise ConnectionError("Godot MCP endpoint belongs to a different project.")
            if endpoint.get("version") != 1 or endpoint.get("host") != "127.0.0.1":
                raise ConnectionError("Unsupported Godot MCP endpoint.")
            self.host = endpoint["host"]
            self.port = int(endpoint["port"])
            self.token = str(endpoint["token"])
        if self.port is None:
            raise ConnectionError("Pass --project to discover this editor's MCP endpoint.")
        try:
            sock = socket.create_connection((self.host, self.port), timeout=self.connect_timeout)
            sock.settimeout(self.request_timeout)
            self.sock = sock
            self.stream = sock.makefile("rwb")
            if self.project:
                state = self._exchange("godot_get_state", {})
                if not state.get("ok") or Path(state["result"]["project_path"]).resolve() != self.project:
                    raise ConnectionError("Connected editor does not match --project.")
        except Exception:
            self.close()
            raise

    def invoke(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self._connect()
        return self._exchange(tool, arguments)

    def _exchange(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        payload = {"id": request_id, "method": "invoke", "token": self.token, "params": {"tool": tool, "arguments": arguments}}
        try:
            assert self.stream is not None
            self.stream.write((json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8"))
            self.stream.flush()
            line = self.stream.readline(8 * 1024 * 1024 + 1)
            if not line:
                raise ConnectionError("Godot editor bridge closed the connection.")
            if len(line) > 8 * 1024 * 1024:
                raise RuntimeError("Godot editor response exceeded 8 MiB.")
            response = json.loads(line)
            if response.get("id") != request_id:
                raise RuntimeError(f"Unexpected bridge response id: {response.get('id')}")
            return response
        except (OSError, ValueError, RuntimeError, ConnectionError):
            self.close()
            raise


def rpc_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def rpc_error(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def tool_result(response: dict[str, Any]) -> dict[str, Any]:
    if not response.get("ok"):
        error = response.get("error", {})
        text = json.dumps(error, ensure_ascii=False)
        return {"content": [{"type": "text", "text": text}], "isError": True}

    result = response.get("result")
    content: list[dict[str, Any]] = [
        {"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}
    ]
    if isinstance(result, dict) and isinstance(result.get("path"), str):
        path = Path(result["path"])
        if path.is_file() and path.suffix.lower() == ".png":
            content.append({"type": "image", "data": base64.b64encode(path.read_bytes()).decode("ascii"), "mimeType": "image/png"})
            try:
                path.unlink()
            except OSError:
                pass
    return {"content": content, "structuredContent": result, "isError": False}


def validate_arguments(value: Any, schema: dict[str, Any], path: str = "arguments") -> None:
    expected = schema.get("type")
    matches = {
        "object": isinstance(value, dict), "array": isinstance(value, list),
        "string": isinstance(value, str), "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
    }
    if expected and not matches[expected]:
        raise ValueError(f"{path} must be {expected}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} is not an allowed value")
    if expected == "object":
        for key in schema.get("required", []):
            if key not in value:
                raise ValueError(f"{path}.{key} is required")
        properties = schema.get("properties", {})
        for key, item in value.items():
            if key in properties:
                validate_arguments(item, properties[key], f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                raise ValueError(f"Unknown {path}.{key}")
    elif expected == "array":
        for index, item in enumerate(value):
            validate_arguments(item, schema.get("items", {}), f"{path}[{index}]")
    elif expected in {"integer", "number"}:
        if not __import__("math").isfinite(value):
            raise ValueError(f"{path} must be finite")
        if value < schema.get("minimum", value) or value > schema.get("maximum", value):
            raise ValueError(f"{path} is outside the allowed range")
    elif expected == "string" and len(value) < schema.get("minLength", 0):
        raise ValueError(f"{path} is too short")


def handle_message(message: dict[str, Any], bridge: GodotBridge) -> dict[str, Any] | None:
    request_id = message.get("id")
    method = message.get("method", "")
    params = message.get("params") or {}

    if method.startswith("notifications/"):
        return None
    if method == "initialize":
        requested = params.get("protocolVersion", "2025-06-18")
        protocol = requested if requested in SUPPORTED_PROTOCOLS else "2025-06-18"
        return rpc_result(request_id, {"protocolVersion": protocol, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": SERVER_INFO})
    if method == "ping":
        return rpc_result(request_id, {})
    if method == "tools/list":
        return rpc_result(request_id, {"tools": TOOLS})
    if method == "tools/call":
        tool = str(params.get("name", ""))
        arguments = params.get("arguments", {})
        if tool not in {item["name"] for item in TOOLS}:
            return rpc_error(request_id, -32602, f"Unknown tool: {tool}")
        try:
            validate_arguments(arguments, next(item["inputSchema"] for item in TOOLS if item["name"] == tool))
            return rpc_result(request_id, tool_result(bridge.invoke(tool, arguments)))
        except Exception as error:
            return rpc_result(request_id, {"content": [{"type": "text", "text": str(error)}], "isError": True})
    return rpc_error(request_id, -32601, f"Method not found: {method}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Godot MCP stdio server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int)
    parser.add_argument("--project", type=Path, help="Godot project directory; discovers endpoint and verifies editor identity.")
    parser.add_argument("--list-tools", action="store_true", help="Print the MCP tool catalog and exit.")
    parser.add_argument("--call", choices=[tool["name"] for tool in TOOLS], help="Invoke a tool once and print the same MCP result as tools/call.")
    parser.add_argument("--arguments", default="{}", help="JSON object for --call.")
    parser.add_argument("--output-image", type=Path, help="Save the image returned by --call godot_capture.")
    parser.add_argument("--connect-timeout", type=float, default=5.0)
    parser.add_argument("--request-timeout", type=float, default=15.0)
    args = parser.parse_args()

    if args.list_tools:
        print(json.dumps({"tools": TOOLS}, ensure_ascii=False, indent=2))
        return 0
    project = args.project
    if project is None:
        # Installed plugins can discover their owning project without client configuration.
        for parent in Path(__file__).resolve().parents:
            if (parent / "project.godot").is_file():
                project = parent
                break
    bridge = GodotBridge(args.host, args.port, args.connect_timeout, args.request_timeout, project)
    try:
        if args.call:
            arguments = json.loads(args.arguments)
            if not isinstance(arguments, dict):
                parser.error("--arguments must be a JSON object")
            result = handle_message({"id": 1, "method": "tools/call", "params": {"name": args.call, "arguments": arguments}}, bridge)["result"]
            if args.output_image:
                for item in result.get("content", []):
                    if item["type"] == "image":
                        args.output_image.write_bytes(base64.b64decode(item["data"]))
                        item["path"] = str(args.output_image.resolve())
                        del item["data"]
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1 if result.get("isError") else 0
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
                response = handle_message(message, bridge)
            except json.JSONDecodeError as error:
                response = rpc_error(None, -32700, "Parse error", str(error))
            except Exception as error:
                response = rpc_error(message.get("id") if isinstance(message, dict) else None, -32603, "Internal error", str(error))
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
                sys.stdout.flush()
    finally:
        bridge.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
