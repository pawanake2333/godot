"""Reclaim only processes running an isolated fixture, even after its editor died."""
from __future__ import annotations

import contextlib
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sidecar"))
from lifecycle import Watch


def fixture_processes(project, executable):
    project_spellings = {str(Path(project)), str(Path(project).resolve())}
    executable = str(Path(executable).resolve())
    output = subprocess.check_output(["/bin/ps", "-ax", "-o", "pid=,command="], text=True)
    found = {}
    for line in output.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        pid, command = int(parts[0]), parts[1]
        if command.startswith(executable + " ") and any(("--path " + project + " ") in (command + " ") for project in project_spellings):
            found[pid] = command
    return found


def cleanup_fixture(project, executable):
    """Kernel exit notifications, with identity revalidation before each signal."""
    exited = []
    for pid, command in fixture_processes(project, executable).items():
        try:
            with contextlib.closing(Watch(Path(project), pid)) as watcher:
                if fixture_processes(project, executable).get(pid) != command:
                    continue
                os.kill(pid, signal.SIGTERM)
                if not wait_exit(watcher, 5):
                    if fixture_processes(project, executable).get(pid) != command:
                        continue
                    os.kill(pid, signal.SIGKILL)
                    if not wait_exit(watcher, 5) and pid in fixture_processes(project, executable):
                        raise RuntimeError(f"Fixture process {pid} did not exit after SIGKILL")
                exited.append(pid)
        except ProcessLookupError:
            pass
    if fixture_processes(project, executable):
        raise RuntimeError("Fixture processes survived cleanup")
    return exited


def wait_exit(watcher, timeout):
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        if any(event["process_exit"] for event in watcher.wait(remaining)):
            return True
