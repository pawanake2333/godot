"""Output display and diagnostic clearing have separate, explicit contracts."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sidecar"))
import godot_mcp_server as server


class OutputContractTests(unittest.TestCase):
    def test_output_modes_accept_only_their_own_fields(self):
        schema = next(tool["inputSchema"] for tool in server.TOOLS if tool["name"] == "godot_output_control")
        for arguments in [
            {}, {"action": "get"},
            {"action": "configure", "visible": True, "levels": [], "search": "marker", "collapse": False},
            {"action": "read", "max_chars": 32},
            {"action": "read", "offset": 32, "expected_revision": 4, "editor_epoch": "editor"},
            {"action": "clear", "editor_epoch": "editor"},
            {"action": "clear", "editor_epoch": "editor", "scope": "diagnostics", "through_cursor": 123},
            {"action": "clear", "editor_epoch": "editor", "scope": "all", "through_cursor": 123},
        ]:
            with self.subTest(arguments=arguments):
                server.validate_arguments(arguments, schema)

    def test_ambiguous_or_unbounded_operations_never_reach_editor(self):
        bridge = Mock()
        for arguments in [
            {"action": "configure"}, {"action": "configure", "levels": ["fatal"]},
            {"action": "configure", "search": "x" * 1025},
            {"action": "get", "scope": "all"},
            {"action": "read", "offset": 1}, {"action": "read", "max_chars": 65537},
            {"action": "read", "offset": 1, "expected_revision": 4},
            {"action": "clear"},
            {"action": "clear", "editor_epoch": "editor", "scope": "diagnostics"},
            {"action": "clear", "editor_epoch": "editor", "scope": "all"},
            {"action": "clear", "editor_epoch": "editor", "through_cursor": 5},
            {"action": "clear", "editor_epoch": "editor", "scope": "all", "through_cursor": -1},
        ]:
            with self.subTest(arguments=arguments):
                response = server.handle_message({"id": 1, "method": "tools/call", "params": {"name": "godot_output_control", "arguments": arguments}}, bridge)
                self.assertTrue(response["result"]["isError"])
        bridge.invoke.assert_not_called()
