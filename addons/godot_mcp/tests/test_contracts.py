"""Contract failures must be rejected before touching an editor document."""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sidecar"))
import godot_mcp_server as server


class ContractTests(unittest.TestCase):
    def test_discriminated_arguments_are_rejected_without_dispatch(self):
        cases = [
            ("godot_input_send", {"session_id": "s", "kind": "key"}),
            ("godot_input_send", {"session_id": "s", "kind": "action_state"}),
            ("godot_input_send", {"session_id": "s", "kind": "text", "text": "ok", "pressed": True}),
            ("godot_input_send", {"session_id": "s", "kind": "joy_motion", "axis": 1}),
            ("godot_input_send", {"session_id": "s", "kind": "text", "text": "x" * 4097}),
            ("godot_edit_batch", {"scene_id": "s", "expected_revision": 0, "operations": [{"op": "rename", "node_path": "."}]}),
            ("godot_edit_batch", {"scene_id": "s", "expected_revision": 0, "node_path": ".", "operations": [{"op": "create", "name": "Child"}]}),
            ("godot_edit_batch", {"scene_id": "s", "expected_revision": 0, "operations": [{"op": "delete", "node_path": "Child", "name": "Unused"}]}),
            ("godot_runtime_wait", {"session_id": "s", "condition": "property", "property": "value", "value": 1}),
            ("godot_script_patch", {"document_id": "d", "expected_hash": "h", "action": "undo", "edits": [{"before": "a", "after": "b"}]}),
            ("godot_animation_edit", {"scene_id": "s", "expected_revision": 0, "operation": "set_key", "track": 0, "time": 1}),
            ("godot_run_wait", {"run_id": "r", "stage": "exited", "minimum_sessions": 2}),
        ]
        for name, arguments in cases:
            with self.subTest(name=name, arguments=arguments):
                bridge = Mock()
                result = server.handle_message({"id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}, bridge)
                self.assertTrue(result["result"]["isError"])
                bridge.invoke.assert_not_called()

    def test_invalid_success_is_reported_as_unknown_outcome(self):
        result = server.checked_result("godot_scene_open", {"ok": True, "result": {"scene_id": "s"}})
        self.assertTrue(result["isError"])
        self.assertIn("RESULT_CONTRACT_VIOLATION", result["content"][0]["text"])
        self.assertIn("do not replay", result["content"][0]["text"])

    def test_nested_and_non_finite_json_are_bounded(self):
        nested = {}
        for _ in range(34):
            nested = {"value": nested}
        for value in [nested, {"value": float("inf")}, {"value": [0] * 4097}]:
            with self.assertRaises(ValueError):
                server.validate_json_budget(value)

    def test_legacy_entry_points_are_removed(self):
        old = ['godot_get_state', 'godot_get_open_scenes', 'godot_close_scene', 'godot_open_scene', 'godot_list_commands', 'godot_run_command', 'godot_play', 'godot_stop', 'godot_capture', 'godot_get_edited_scene', 'godot_inspect_editor_node', 'godot_set_editor_property', 'godot_save_scene', 'godot_get_runtime_tree', 'godot_inspect_runtime_object', 'godot_set_runtime_property', 'godot_send_input_action', 'godot_create_editor_node', 'godot_delete_editor_node', 'godot_editor_undo', 'godot_editor_redo', 'godot_reload_project', 'godot_get_logs', 'godot_send_mouse_event', 'godot_wait_runtime_frames']
        names = {tool["name"] for tool in server.TOOLS}
        self.assertEqual(len(names), 63)
        self.assertFalse(names.intersection(old))

    def test_every_tool_has_a_success_output_schema(self):
        names = [tool["name"] for tool in server.TOOLS]
        self.assertEqual(len(names), len(set(names)))
        for tool in server.TOOLS:
            self.assertEqual(tool["outputSchema"]["type"], "object")


if __name__ == "__main__":
    unittest.main()
