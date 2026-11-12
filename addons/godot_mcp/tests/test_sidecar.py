#!/usr/bin/env python3
"""Transport and argument checks which do not require a running editor."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    "godot_mcp_server", Path(__file__).resolve().parents[1] / "sidecar/godot_mcp_server.py"
)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class SidecarTests(unittest.TestCase):
    def test_catalog_is_available_without_editor(self):
        bridge = Mock()
        response = server.handle_message({"id": 1, "method": "tools/list"}, bridge)
        self.assertEqual(len(response["result"]["tools"]), 24)
        bridge.invoke.assert_not_called()

    def test_local_discovery_and_capabilities_do_not_require_editor(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = server.GodotBridge("127.0.0.1", None, 1, 1, Path(directory))
            try:
                for name in ("godot_capabilities_get", "godot_tools_discover"):
                    response = server.handle_message({"id": 1, "method": "tools/call", "params": {"name": name, "arguments": {}}}, bridge)
                    self.assertFalse(response["result"]["isError"])
                response = server.handle_message({"id": 1, "method": "tools/call", "params": {"name": "godot_tools_call", "arguments": {"tool_name": "godot_tools_call", "arguments": {}}}}, bridge)
                self.assertTrue(response["result"]["isError"])
            finally:
                bridge.close()
                bridge._services.close()

    def test_headless_cli_workflows_are_not_mcp_tools(self):
        bridge = Mock()
        for name in ("godot_script_run", "godot_tests_run", "godot_script_validate", "godot_asset_bake", "godot_export_run", "godot_image_compare", "godot_files_search", "godot_resource_dependencies", "godot_scene_validate", "godot_script_create"):
            response = server.handle_message({"id": 1, "method": "tools/call", "params": {"name": name, "arguments": {}}}, bridge)
            self.assertEqual(response["error"]["code"], -32602)
        bridge.invoke.assert_not_called()

    def test_batch_size_limits_are_enforced_before_editor_dispatch(self):
        bridge = Mock()
        for size in (0, 257):
            result = server.handle_message({"id": 1, "method": "tools/call", "params": {"name": "godot_edit_batch", "arguments": {"scene_id": "scene", "expected_revision": 0, "operations": [{"op": "create", "name": "Child"}] * size}}}, bridge)
            self.assertTrue(result["result"]["isError"])
        bridge.invoke.assert_not_called()

    def test_invalid_arguments_never_reach_editor(self):
        bridge = Mock()
        for arguments in [
            {"kind": "keyboard"}, {"kind": "button", "button": True},
            {"kind": "motion", "delta_x": float("nan")},
            {"kind": "motion", "delta_x": "12"}, {"kind": "motion", "unknown": 1},
            [], None,
        ]:
            with self.subTest(arguments=arguments):
                response = server.handle_message({
                    "id": 1, "method": "tools/call",
                    "params": {"name": "godot_input_send", "arguments": arguments},
                }, bridge)
                self.assertTrue(response["result"]["isError"])
        bridge.invoke.assert_not_called()

    def test_missing_endpoint_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = server.GodotBridge("127.0.0.1", None, 1, 1, Path(directory))
            with self.assertRaisesRegex(ConnectionError, "Start the custom editor"):
                bridge.invoke("_editor_state", {})

    def test_wrong_project_endpoint_is_rejected_before_connect(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            endpoint = project / ".godot/mcp/endpoint.json"
            endpoint.parent.mkdir(parents=True)
            endpoint.write_text(json.dumps({"project_path": str(project / "other")}))
            bridge = server.GodotBridge("127.0.0.1", None, 1, 1, project)
            with patch.object(server.socket, "create_connection") as connect:
                with self.assertRaisesRegex(ConnectionError, "different project"):
                    bridge.invoke("_editor_state", {})
                connect.assert_not_called()

    def test_transport_failure_is_not_replayed(self):
        sock = Mock()
        stream = sock.makefile.return_value
        stream.readline.side_effect = TimeoutError("Request expired")
        bridge = server.GodotBridge("127.0.0.1", 6077, 1, 1)
        with patch.object(server.socket, "create_connection", return_value=sock) as connect:
            with self.assertRaises(TimeoutError):
                bridge.invoke("godot_create_editor_node", {"name": "OnlyOnce"})
            self.assertEqual(stream.write.call_count, 1)
            self.assertEqual(connect.call_count, 1)
            self.assertIsNone(bridge.sock)
            stream.close.assert_called_once()

    def test_discovery_and_connected_project_are_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            endpoint = project / ".godot/mcp/endpoint.json"
            endpoint.parent.mkdir(parents=True)
            endpoint.write_text(json.dumps({
                "version": 1, "project_path": str(project),
                "host": "127.0.0.1", "port": 12345, "token": "fixture-token",
            }))
            sock = Mock()
            stream = sock.makefile.return_value
            stream.readline.side_effect = [
                json.dumps({"id": 1, "ok": True, "result": {"project_path": str(project / "other")}}).encode() + b"\n",
            ]
            bridge = server.GodotBridge("127.0.0.1", None, 1, 1, project)
            with patch.object(server.socket, "create_connection", return_value=sock) as connect:
                with self.assertRaisesRegex(ConnectionError, "does not match"):
                    bridge.invoke("godot_delete_editor_node", {"node_path": "Target"})
                connect.assert_called_once_with(("127.0.0.1", 12345), timeout=1)
                self.assertEqual(stream.write.call_count, 1)
                request = json.loads(stream.write.call_args.args[0])
                self.assertEqual(request["token"], "fixture-token")
                self.assertEqual(request["params"]["tool"], "_editor_state")


if __name__ == "__main__":
    unittest.main()
