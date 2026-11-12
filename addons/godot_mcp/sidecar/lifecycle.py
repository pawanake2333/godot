"""Editor lifecycle using authenticated preflight and kernel notifications."""
from __future__ import annotations

import contextlib
import os
from pathlib import Path
import select
import selectors
import ctypes
import sys
import subprocess
import time


class Watch:
    """Wait for directory/process events; use kernel deadline timeouts."""
    def __init__(self, directory: Path, pid=None):
        self.directory = directory
        self.descriptors = []
        self.pid = None
        self.queue = None
        self.selector = None
        self.endpoint_inode = None
        try:
            if hasattr(select, "kqueue"):
                self.queue = select.kqueue()
                descriptor = os.open(directory, os.O_RDONLY)
                self.descriptors.append(descriptor)
                self.queue.control([select.kevent(descriptor, filter=select.KQ_FILTER_VNODE, flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR, fflags=select.KQ_NOTE_WRITE | select.KQ_NOTE_RENAME)], 0, 0)
            elif sys.platform == "linux":
                self.selector = selectors.DefaultSelector()
                library = ctypes.CDLL(None, use_errno=True)
                descriptor = library.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
                if descriptor < 0:
                    raise OSError(ctypes.get_errno(), "inotify_init1")
                self.descriptors.append(descriptor)
                if library.inotify_add_watch(descriptor, os.fsencode(directory), 0x00000100 | 0x00000008 | 0x00000080 | 0x00000200 | 0x00000002) < 0:
                    raise OSError(ctypes.get_errno(), "inotify_add_watch")
                self.selector.register(descriptor, selectors.EVENT_READ, "directory")
            else:
                raise RuntimeError("Lifecycle kernel notifications require macOS or Linux")
            if pid is not None:
                self.watch_process(pid)
        except Exception:
            self.close()
            raise

    def watch_process(self, pid):
        self.pid = pid
        if self.queue is not None:
            self.queue.control([select.kevent(pid, filter=select.KQ_FILTER_PROC, flags=select.KQ_EV_ADD, fflags=select.KQ_NOTE_EXIT)], 0, 0)
        else:
            descriptor = os.pidfd_open(pid)
            self.descriptors.append(descriptor)
            self.selector.register(descriptor, selectors.EVENT_READ, "process")

    def watch_endpoint(self):
        if self.queue is None:
            return
        path = self.directory / "endpoint.json"
        try:
            identity = path.stat().st_ino
            if identity == self.endpoint_inode:
                return
            descriptor = os.open(path, os.O_RDONLY)
            self.descriptors.append(descriptor)
            self.queue.control([select.kevent(descriptor, filter=select.KQ_FILTER_VNODE, flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR, fflags=select.KQ_NOTE_WRITE | select.KQ_NOTE_DELETE | select.KQ_NOTE_RENAME)], 0, 0)
            self.endpoint_inode = identity
        except FileNotFoundError:
            pass

    def wait(self, seconds):
        if self.queue is not None:
            return [{"process_exit": event.filter == select.KQ_FILTER_PROC} for event in self.queue.control(None, 8, max(0, seconds))]
        events = []
        for key, _ in self.selector.select(max(0, seconds)):
            process_exit = key.data == "process"
            if not process_exit:
                try:
                    os.read(key.fd, 65536)
                except BlockingIOError:
                    pass
            events.append({"process_exit": process_exit})
        return events

    def close(self):
        if self.queue is not None:
            self.queue.close()
        if self.selector is not None:
            self.selector.close()
        for descriptor in self.descriptors:
            os.close(descriptor)
        self.descriptors.clear()


class Lifecycle:
    def __init__(self, service):
        self.service = service
        self.editors = {}

    def start(self, args, editor):
        service = self.service
        if service.project is None or not service.executable:
            raise ValueError("Configure --project and --godot")
        try:
            state = editor("_editor_state", {})
        except (OSError, ValueError, RuntimeError) as error:
            if "package/protocol mismatch" in str(error):
                raise
            state = None
        if state is not None:
            if not args.get("reuse", True):
                raise ValueError("An editor is already active for this project")
            return {"reused": True, "state": editor("_editor_ready", {})}
        directory = service.project / ".godot/mcp"
        directory.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(Watch(directory)) as watcher:
            output = Path(service.storage.name) / ("editor-" + os.urandom(8).hex() + ".log")
            mode = args.get("display_mode", "headless_logic")
            if mode not in {"headless_logic", "visible"}:
                raise ValueError("Editor display mode not implemented")
            command = [str(service.executable), *(["--headless"] if mode == "headless_logic" else []), "--editor", "--path", str(service.project)]
            with output.open("wb") as stream:
                process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
            self.editors[process.pid] = process
            if process.poll() is None:
                watcher.watch_process(process.pid)
            deadline = time.monotonic() + args.get("timeout_ms", 15000) / 1000
            while True:
                if process.poll() is not None:
                    raise RuntimeError(f"Editor exited ({process.returncode}): {output.read_text(errors='replace')[-16384:]}")
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Editor endpoint deadline (editor PID {process.pid} remains open to preserve human edits): {output.read_text(errors='replace')[-16384:]}")
                watcher.watch_endpoint()
                try:
                    state = editor("_editor_state", {})
                    if state.get("editor_pid") == process.pid:
                        try:
                            return {"reused": False, "state": editor("_editor_ready", {}), "display_mode": mode}
                        except RuntimeError as error:
                            if "EDITOR_NOT_READY" in str(error):
                                continue
                            raise
                except (OSError, ValueError, RuntimeError):
                    pass
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"Editor endpoint deadline (editor PID {process.pid} remains open to preserve human edits): {output.read_text(errors='replace')[-16384:]}")
                watcher.wait(remaining)

    def close(self, args, editor):
        state = editor("_editor_state", {})
        if state.get("editor_epoch") != args["editor_epoch"]:
            raise ValueError("Editor epoch changed")
        if state.get("document_revision") != args["expected_document_revision"]:
            raise ValueError("DOCUMENT_CONFLICT: Open documents changed since the supplied editor status")
        directory = self.service.project / ".godot/mcp"
        pid = state["editor_pid"]
        with contextlib.closing(Watch(directory, pid)) as watcher:
            result = editor("godot_editor_commit_close", {"editor_epoch": args["editor_epoch"], "expected_document_revision": args["expected_document_revision"], "unsaved": args.get("unsaved", "reject")})
            deadline = time.monotonic() + args.get("timeout_ms", 15000) / 1000
            while True:
                process = self.editors.get(pid)
                if process is not None and process.poll() is not None:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Editor did not exit after accepting close")
                events = watcher.wait(remaining)
                if any(event["process_exit"] for event in events):
                    break
            process = self.editors.pop(pid, None)
            if process is not None:
                process.wait(timeout=1)
        return {"closed": True, "editor_pid": pid, "documents": result.get("documents", []), "active_scene": result.get("active_scene", ""), "script_documents": result.get("script_documents", []), "active_script": result.get("active_script", "")}

    def restart(self, args, editor):
        closed = self.close(args, editor)
        started = self.start({**args, "reuse": False}, editor)
        for path in closed["documents"]:
            if path:
                editor("godot_scene_open", {"path": path})
        if closed["active_scene"]:
            editor("godot_scene_open", {"path": closed["active_scene"]})
        scripts = editor("_restore_script_documents", {"paths": closed["script_documents"], "active": closed["active_script"]})
        return {"closed": closed, "started": started, "script_documents": scripts}
