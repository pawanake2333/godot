"""Verify fixture cleanup without touching unrelated development processes."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from process_cleanup import cleanup_fixture, fixture_processes


@unittest.skipUnless(sys.platform in {"darwin", "linux"} and os.environ.get("GODOT_MCP_PROCESS_TESTS") == "1", "Opt-in process integration test requires process-table access")
class FixtureCleanupTests(unittest.TestCase):
    def test_reclaims_only_the_exact_fixture(self):
        with tempfile.TemporaryDirectory(prefix="godot-mcp-cleanup-") as root:
            target = Path(root) / "target"
            other = Path(root) / "other"
            target.mkdir()
            other.mkdir()
            executable = Path(sys.executable).resolve()
            command = [str(executable), "-c", "import os; os.read(0, 1)", "--path"]
            owned = subprocess.Popen([*command, str(target)], stdin=subprocess.PIPE)
            unrelated = subprocess.Popen([*command, str(other)], stdin=subprocess.PIPE)
            try:
                self.assertIn(owned.pid, fixture_processes(target, executable))
                self.assertNotIn(unrelated.pid, fixture_processes(target, executable))
                self.assertIn(owned.pid, cleanup_fixture(target, executable))
                owned.wait(timeout=5)
                self.assertIsNone(unrelated.poll())
                self.assertEqual(fixture_processes(target, executable), {})
            finally:
                for process in [owned, unrelated]:
                    if process.poll() is None:
                        process.terminate()
                    process.wait(timeout=5)
                    process.stdin.close()


if __name__ == "__main__":
    unittest.main()
