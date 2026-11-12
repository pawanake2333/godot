"""Default catalog size, lazy discovery and the shared validated dispatch path."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sidecar"))
import godot_mcp_server as server
from tool_policy import CORE_TOOL_NAMES


class ToolPolicyTests(unittest.TestCase):
    def test_default_catalog_is_small_and_full_catalog_is_explicit(self):
        bridge = server.GodotBridge("127.0.0.1", None, 1, 1)
        default = server.handle_message({"id": 1, "method": "tools/list"}, bridge)["result"]["tools"]
        self.assertEqual({tool["name"] for tool in default}, CORE_TOOL_NAMES)
        self.assertEqual(len(default), 24)
        bridge.toolset = "all"
        full = server.handle_message({"id": 2, "method": "tools/list"}, bridge)["result"]["tools"]
        self.assertEqual(len(full), 62)
        self.assertLess(len(json.dumps(default)), len(json.dumps(full)) * .6)

    def test_lazy_discovery_and_extended_dispatch_use_existing_schemas(self):
        bridge = server.GodotBridge("127.0.0.1", None, 1, 1)
        bridge.invoke = Mock(return_value={"ok": True, "result": {"scene_id": "new", "revision": 0}})
        def call(name, args):
            return server.handle_message({"id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}, bridge)["result"]
        try:
            summary = call("godot_tools_discover", {})["structuredContent"]
            self.assertEqual(len(summary["tools"]), 20)
            self.assertTrue(summary["has_more"])
            self.assertNotIn("inputSchema", summary["tools"][0])
            discovered = call("godot_tools_discover", {"query": "godot_scene_create", "tier": "extended", "include_schema": True})["structuredContent"]["tools"]
            self.assertEqual(len(discovered), 1)
            self.assertEqual(discovered[0]["_meta"]["godot/tier"], "extended")
            self.assertIn("inputSchema", discovered[0])
            failed = call("godot_tools_call", {"tool_name": "godot_scene_create", "arguments": {}})
            self.assertTrue(failed["isError"])
            bridge.invoke.assert_not_called()
            success = call("godot_tools_call", {"tool_name": "godot_scene_create", "arguments": {"root_type": "Node", "root_name": "Probe"}})
            self.assertEqual(success["structuredContent"]["scene_id"], "new")
            bridge.invoke.assert_called_once()
        finally:
            bridge.close()
            bridge._services.close()

    def test_single_node_aliases_are_removed_and_not_submitted(self):
        bridge = Mock()
        for action in ["create", "delete", "rename", "duplicate", "reparent", "attach_script", "connect_signal", "disconnect_signal", "groups_set"]:
            response = server.handle_message({"id": 1, "method": "tools/call", "params": {"name": "godot_node_" + action, "arguments": {}}}, bridge)
            self.assertEqual(response["error"]["code"], -32602)
        bridge.invoke.assert_not_called()
