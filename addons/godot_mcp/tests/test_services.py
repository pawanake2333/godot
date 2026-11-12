"""Operation cancellation and event cursor semantics without editor mocks."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sidecar"))
from mcp_services import Services


class ServiceTests(unittest.TestCase):
    def test_queued_cancel_has_no_side_effects(self):
        service = Services(None)
        effects = []
        try:
            with service.condition:
                job = service.submit_operation("probe", lambda: effects.append("applied"))
                self.assertEqual(job["state"], "queued")
                cancelled = service.invoke("godot_job_cancel", {"job_id": job["job_id"]}, [], None, None)
                self.assertEqual(cancelled["state"], "cancelled")
                self.assertTrue(cancelled["cancellation_accepted"])
                self.assertFalse(cancelled["side_effects_applied"])
        finally:
            service.close()
        self.assertEqual(effects, [])

    def test_events_filter_and_report_overflow_without_rewinding(self):
        service = Services(None)
        try:
            for _ in range(2050):
                service.emit_event("job_state", job_id="other", state="running")
            service.emit_event("run_stage", session_id="target", stage="first_physics")
            result = service.invoke("godot_events_read", {"cursor": 0, "session_id": "target", "kinds": ["run_stage"]}, [], None, None)
            self.assertTrue(result["dropped"])
            self.assertEqual(len(result["events"]), 1)
            self.assertEqual(result["next_cursor"], 2051)
            self.assertEqual(result["events"][0]["stage"], "first_physics")
        finally:
            service.close()


if __name__ == "__main__":
    unittest.main()
