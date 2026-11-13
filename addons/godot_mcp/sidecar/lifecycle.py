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
        self.win_handles = []
        try:
            if sys.platform == "win32":
                from ctypes import wintypes
                self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                self.kernel.FindFirstChangeNotificationW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL, wintypes.DWORD]
                self.kernel.FindFirstChangeNotificationW.restype = wintypes.HANDLE
                self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                self.kernel.OpenProcess.restype = wintypes.HANDLE
                self.kernel.WaitForMultipleObjects.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE), wintypes.BOOL, wintypes.DWORD]
                self.kernel.WaitForMultipleObjects.restype = wintypes.DWORD
                self.kernel.FindNextChangeNotification.argtypes = [wintypes.HANDLE]
                self.kernel.FindCloseChangeNotification.argtypes = [wintypes.HANDLE]
                self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
                handle = self.kernel.FindFirstChangeNotificationW(str(directory), False, 0x1 | 0x8 | 0x10)
                if handle == ctypes.c_void_p(-1).value:
                    raise ctypes.WinError(ctypes.get_last_error())
                self.win_handles.append(handle)
            elif hasattr(select, "kqueue"):
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
        if self.win_handles:
            handle = self.kernel.OpenProcess(0x00100000, False, pid)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            self.win_handles.append(handle)
        elif self.queue is not None:
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
        if self.win_handles:
            from ctypes import wintypes
            handles = (wintypes.HANDLE * len(self.win_handles))(*self.win_handles)
            result = self.kernel.WaitForMultipleObjects(len(handles), handles, False, min(0xfffffffe, max(0, int(seconds * 1000))))
            if result == 0xffffffff:
                raise ctypes.WinError(ctypes.get_last_error())
            if result == 0:
                if not self.kernel.FindNextChangeNotification(self.win_handles[0]):
                    raise ctypes.WinError(ctypes.get_last_error())
                return [{"process_exit": False}]
            return [{"process_exit": True}] if result == 1 and len(handles) > 1 else []
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
        if self.win_handles:
            self.kernel.FindCloseChangeNotification(self.win_handles[0])
            for handle in self.win_handles[1:]:
                self.kernel.CloseHandle(handle)
            self.win_handles.clear()
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
