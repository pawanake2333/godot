import contextlib
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sidecar"))
from lifecycle import Watch
from mcp_services import Services


@unittest.skipUnless(sys.platform == "win32", "Windows kernel notification test")
class WindowsWatchTests(unittest.TestCase):
    def test_console_launcher_uses_matching_editor(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "godot.exe"
            executable.touch()
            service = Services(None, executable.with_name("godot.console.exe"))
            try:
                self.assertEqual(service.executable, executable)
            finally:
                service.close()

    def test_forced_sidecar_exit_reaps_owned_process(self):
        script = """
import subprocess, sys
from windows_job import WindowsJob
job = WindowsJob()
child = subprocess.Popen([sys.executable, '-c', 'import threading; threading.Event().wait()'])
job.assign(child)
print(child.pid, flush=True)
sys.stdin.read()
"""
        sidecar = subprocess.Popen([sys.executable, "-u", "-c", script], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, text=True,
                                   cwd=str(Path(__file__).resolve().parents[1] / "sidecar"))
        try:
            pid = int(sidecar.stdout.readline())
            with tempfile.TemporaryDirectory() as directory:
                with contextlib.closing(Watch(Path(directory), pid)) as watcher:
                    sidecar.terminate()
                    sidecar.wait(timeout=5)
                    deadline = time.monotonic() + 5
                    while True:
                        if any(event["process_exit"] for event in watcher.wait(max(0, deadline - time.monotonic()))):
                            break
                        self.assertLess(time.monotonic(), deadline)
        finally:
            if sidecar.poll() is None:
                sidecar.terminate()
                sidecar.wait(timeout=5)
            sidecar.stdin.close()
            sidecar.stdout.close()

    def test_directory_and_process_notifications(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with contextlib.closing(Watch(path)) as watcher:
                (path / "endpoint.json").write_text("ready")
                self.assertEqual(watcher.wait(5), [{"process_exit": False}])
                process = subprocess.Popen([sys.executable, "-c", "pass"])
                try:
                    watcher.watch_process(process.pid)
                    deadline = time.monotonic() + 5
                    while True:
                        events = watcher.wait(max(0, deadline - time.monotonic()))
                        if any(event["process_exit"] for event in events):
                            break
                        self.assertLess(time.monotonic(), deadline)
                finally:
                    process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
