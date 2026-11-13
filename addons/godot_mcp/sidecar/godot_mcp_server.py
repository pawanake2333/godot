#!/usr/bin/env python3
"""Dependency-free MCP stdio server for the Godot MCP editor addon."""

from __future__ import annotations

import argparse
import hashlib
import copy
import base64
import json
import socket
import signal
import threading
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_services import Services, LOCAL_TOOLS
from contracts import configure_catalog, validate_arguments, validate_json_budget
from editor_catalog import EDITOR_TOOLS
from rpc_connection import RPCConnection
from stdio_transport import serve as serve_stdio
from tool_registry import ToolRegistry
from package_info import VERSION, PROTOCOL, source_identity
from tool_policy import select_tools

SERVER_INFO = {"name": "godot-mcp", "version": VERSION}
SUPPORTED_PROTOCOLS = {"2024-11-05", "2025-03-26", "2025-06-18"}

TOOLS = []
TOOLS.extend(copy.deepcopy(LOCAL_TOOLS))
TOOLS.extend(copy.deepcopy(EDITOR_TOOLS))
configure_catalog(TOOLS)
REGISTRY = ToolRegistry()
_local_names = {entry["name"] for entry in LOCAL_TOOLS}
REGISTRY.register_provider("session", [entry for entry in TOOLS if entry["name"] in _local_names], lambda name, args, bridge: _invoke_local(name, args, bridge))
REGISTRY.register_provider("editor", [entry for entry in TOOLS if entry["name"] not in _local_names], lambda name, args, bridge: bridge.invoke(name, args))
TOOLS = REGISTRY.tools


class GodotBridge:
    def __init__(self, host: str, port: int | None, connect_timeout: float, request_timeout: float,
                 project: Path | None = None, toolset: str = "core"):
        self._lock = threading.RLock()
        self.context = {}
        self._services_lock = threading.RLock()
        self._connection = None
        self._shutting_down = False
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self.request_timeout = request_timeout
        self.sock: socket.socket | None = None
        self.stream = None
        self.next_id = 1
        self.project = project.resolve() if project else None
        self.token = ""
        self.toolset = toolset
        self.package = source_identity()

    def _disconnected(self, connection):
        # A stale reader must not close a newer editor connection.
        with self._lock:
            if self._connection is connection:
                self._connection = None
                self.sock = None
                self.stream = None

    def _context_received(self, context):
        self.context = context

    def close(self) -> None:
        with self._lock:
            connection = self._connection
            if connection is None:
                if self.stream is not None:
                    self.stream.close()
                    self.stream = None
                if self.sock is not None:
                    self.sock.close()
                    self.sock = None
        if connection is not None:
            connection.close()

    def shutdown(self):
        with self._lock:
            self._shutting_down = True
        if "_services" in self.__dict__:
            self._services.begin_shutdown()
        self.close()

    def _connect(self) -> None:
        if self._shutting_down:
            raise ConnectionError("MCP transport is shutting down; request was not submitted")
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
                state = self._exchange_sync("_editor_state", {})
                if not state.get("ok") or Path(state["result"]["project_path"]).resolve() != self.project:
                    raise ConnectionError("Connected editor does not match --project.")
                package = state["result"].get("package", {})
                if state["result"].get("mcp_version") != VERSION or package.get("protocol") != PROTOCOL or not package.get("verified") or package.get("source_sha256") != self.package["source_sha256"]:
                    raise ConnectionError("MCP package/protocol mismatch. Regenerate package.json, synchronize the plugin and restart the editor; mutations were not submitted.")
            sock.settimeout(None)
            self._connection = RPCConnection(sock, self.stream, self._disconnected, self._context_received)
        except Exception:
            self.close()
            raise

    def invoke(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._connect()
            connection = self._connection
            request_id = self.next_id
            self.next_id += 1
        payload = {"id": request_id, "method": "invoke", "token": self.token, "params": {"tool": tool, "arguments": arguments}}
        return connection.request(payload, self.request_timeout)

    def _exchange_sync(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        payload = {"id": request_id, "method": "invoke", "token": self.token, "params": {"tool": tool, "arguments": arguments}}
        try:
            assert self.stream is not None
            self.stream.write((json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
            self.stream.flush()
            line = self.stream.readline(8 * 1024 * 1024 + 1)
            if not line:
                raise ConnectionError("Godot editor bridge closed the connection.")
            if len(line) > 8 * 1024 * 1024:
                raise RuntimeError("Godot editor response exceeded 8 MiB.")
            response = json.loads(line)
            if response.get("id") != request_id:
                raise RuntimeError(f"Unexpected bridge response id: {response.get('id')}")
            self.context = response.get("context", self.context)
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
        return {"content": [{"type": "text", "text": text}], "isError": True, "_meta": {"godot": response.get("context", {})}}

    result = response.get("result")
    native_image = None
    if isinstance(result, dict) and "_mcp_image_base64" in result:
        result = result.copy()
        native_image = result.pop("_mcp_image_base64")
    content: list[dict[str, Any]] = [
        {"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}
    ]
    if native_image is not None:
        content.append({"type": "image", "data": native_image, "mimeType": "image/png"})
    if isinstance(result, dict) and isinstance(result.get("path"), str):
        path = Path(result["path"])
        if path.is_file() and path.suffix.lower() == ".png":
            content.append({"type": "image", "data": base64.b64encode(path.read_bytes()).decode("ascii"), "mimeType": "image/png"})
            try:
                path.unlink()
            except OSError:
                pass
    return {"content": content, "structuredContent": result, "isError": False, "_meta": {"godot": response.get("context", {})}}


def checked_result(name, response):
    if response.get("ok"):
        try:
            validate_json_budget(response.get("result"), 8 * 1024 * 1024)
            validate_arguments(response.get("result"), next(item["outputSchema"] for item in TOOLS if item["name"] == name), "result")
        except ValueError as error:
            return tool_result({"ok": False, "error": {"code": "RESULT_CONTRACT_VIOLATION", "message": str(error), "tool": name, "stage": "result", "outcome": "Inspect current state; do not replay mutations automatically"}})
    return tool_result(response)


def _invoke_local(tool, arguments, bridge):
    with bridge._services_lock:
        if "_services" not in bridge.__dict__:
            bridge._services = Services(bridge.project, getattr(bridge, "godot", None), bridge.toolset)
    def editor(name, values):
        response = bridge.invoke(name, values)
        if not response.get("ok"):
            raise RuntimeError(response.get("error"))
        return response.get("result")
    def dispatch(name, values):
        result = handle_message({"id": 0, "method": "tools/call", "params": {"name": name, "arguments": values}}, bridge)
        payload = result.get("result", {})
        if payload.get("isError") or "error" in result:
            raise ValueError(payload.get("content", result.get("error")))
        return payload.get("structuredContent")
    value = bridge._services.invoke(tool, arguments, TOOLS, dispatch, editor)
    return {"ok": True, "result": value, "context": {"scope": "sidecar", "protocol": 2, "service_id": bridge._services.service_id, "project_id": hashlib.sha256(str(bridge.project).encode()).hexdigest() if bridge.project else None}}


def handle_message(message: dict[str, Any], bridge: GodotBridge) -> dict[str, Any] | None:
    request_id = message.get("id")
    method = message.get("method", "")
    params = message.get("params") or {}

    if method.startswith("notifications/"):
        return None
    if method == "initialize":
        REGISTRY.freeze()
        requested = params.get("protocolVersion", "2025-06-18")
        protocol = requested if requested in SUPPORTED_PROTOCOLS else "2025-06-18"
        return rpc_result(request_id, {"protocolVersion": protocol, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": SERVER_INFO})
    if method == "ping":
        return rpc_result(request_id, {})
    if method == "tools/list":
        return rpc_result(request_id, {"tools": select_tools(TOOLS, "all" if getattr(bridge, "toolset", "core") == "all" else "core")})
    if method == "tools/call":
        tool = str(params.get("name", ""))
        arguments = params.get("arguments", {})
        if tool not in {item["name"] for item in TOOLS}:
            return rpc_error(request_id, -32602, f"Unknown tool: {tool}")
        try:
            validate_json_budget(arguments)
            validate_arguments(arguments, next(item["inputSchema"] for item in TOOLS if item["name"] == tool))
            return rpc_result(request_id, checked_result(tool, REGISTRY.invoke(tool, arguments, bridge)))
        except Exception as error:
            return rpc_result(request_id, tool_result({"ok": False, "error": {"code": "INVALID_ARGUMENT" if isinstance(error, ValueError) else "TRANSPORT_ERROR" if isinstance(error, OSError) else "TOOL_ERROR", "message": str(error), "tool": tool, "stage": "sidecar"}}))
    return rpc_error(request_id, -32601, f"Method not found: {method}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Godot MCP stdio server")
    parser.add_argument("--godot", type=Path, help="Custom editor executable for isolated development tasks.")
    parser.add_argument("--auto-start", action="store_true", help="Start or reuse the editor as a background job; stdio initialization remains available.")
    parser.add_argument("--startup-timeout-ms", type=int, default=60000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int)
    parser.add_argument("--project", type=Path, help="Godot project directory; discovers endpoint and verifies editor identity.")
    parser.add_argument("--list-tools", action="store_true", help="Print the MCP tool catalog and exit.")
    parser.add_argument("--toolset", choices=["core", "all"], default="core", help="Default core catalog or every stateful tool; extended tools remain available through discover/call.")
    parser.add_argument("--call", choices=[tool["name"] for tool in TOOLS], help="Invoke a tool once and print the same MCP result as tools/call.")
    parser.add_argument("--arguments", default="{}", help="JSON object for --call.")
    parser.add_argument("--output-image", type=Path, help="Save the image returned by --call godot_capture_frame.")
    parser.add_argument("--connect-timeout", type=float, default=5.0)
    parser.add_argument("--request-timeout", type=float, default=15.0)
    args = parser.parse_args()

    if args.list_tools:
        print(json.dumps({"tools": select_tools(TOOLS, args.toolset)}, ensure_ascii=False, indent=2))
        return 0
    project = args.project
    if project is None:
        # Installed plugins can discover their owning project without client configuration.
        for parent in Path(__file__).resolve().parents:
            if (parent / "project.godot").is_file():
                project = parent
                break
    bridge = GodotBridge(args.host, args.port, args.connect_timeout, args.request_timeout, project, args.toolset)
    bridge.godot = args.godot
    def terminate(signum, _frame):
        bridge.shutdown()
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, terminate)
    try:
        if args.auto_start:
            startup = _invoke_local("godot_editor_start", {"reuse": True, "display_mode": "visible", "timeout_ms": args.startup_timeout_ms}, bridge)
            bridge._services.startup_job = startup["result"]["job_id"]
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
        serve_stdio(sys.stdin, sys.stdout, lambda message: handle_message(message, bridge), rpc_error, bridge.shutdown)
    finally:
        bridge.shutdown()
        if "_services" in bridge.__dict__:
            bridge._services.close()
        bridge.close()
    return 0


if __name__ == "__main__":
    # MCP stdio is UTF-8 regardless of the Windows system code page.
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
