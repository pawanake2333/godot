"""Native reload rejects invalid requests before dispatch and completes via jobs."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sidecar"))
import godot_mcp_server as server
from mcp_services import Services


class ExtensionContractTests(unittest.TestCase):
    def test_invalid_requests_do_not_reach_editor(self):
        valid = {"path": "res://probe.gdextension", "editor_epoch": "epoch", "expected_document_revision": "revision"}
        for arguments in [{}, {**valid, "path": "res://probe.gd"}, {**valid, "expected_library_hash": "bad"}, {**valid, "expected_classes": {"Probe": [3]}}, {**valid, "force": True}]:
            bridge = Mock()
            response = server.handle_message({"id": 1, "method": "tools/call", "params": {"name": "godot_extension_reload", "arguments": arguments}}, bridge)
            self.assertTrue(response["result"]["isError"])
            bridge.invoke.assert_not_called()

    def test_job_result_waits_for_editor_validation(self):
        service = Services(None)
        editor = Mock(return_value={"reloaded": True, "verified": True})
        try:
            job = service.invoke("godot_extension_reload", {"path": "res://probe.gdextension"}, [], None, editor)
            done = service.invoke("godot_job_wait", {"job_id": job["job_id"], "timeout_ms": 1000}, [], None, editor)
            self.assertEqual(done["state"], "succeeded")
            self.assertTrue(done["result"]["verified"])
            self.assertEqual(editor.call_count, 2)
        finally:
            service.close()


if __name__ == "__main__":
    unittest.main()
