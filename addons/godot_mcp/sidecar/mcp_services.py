"""Stateful editor/run coordination, notifications and screenshot artifacts."""
from __future__ import annotations

from collections import deque
import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import threading
import uuid
from lifecycle import Lifecycle
from runs import Runs
from package_info import source_identity, VERSION, PROTOCOL
from tool_policy import CORE_TOOL_NAMES


def schema(properties=None, required=()):
    return {"type": "object", "properties": properties or {}, "required": list(required), "additionalProperties": False}


def tool(name, description, properties=None, required=()):
    return {"name": name, "description": description, "inputSchema": schema(properties, required)}


STRING = {"type": "string", "minLength": 1}
TIMEOUT = {"type": "integer", "minimum": 1, "maximum": 3600000}
LOCAL_TOOLS = [
    tool("godot_extension_reload", "Reload a project GDExtension through the native editor API and verify native class/method registration; stop games and save documents first.", {
        "path": {"type": "string", "pattern": "^res://.+\\.gdextension$"},
        "editor_epoch": STRING,
        "expected_document_revision": STRING,
        "expected_library_hash": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
        "expected_classes": {"type": "object", "maxProperties": 256, "additionalProperties": {"type": "array", "maxItems": 256, "items": STRING}},
    }, ["path", "editor_epoch", "expected_document_revision"]),
    tool("godot_capabilities_get", "Report actual local tool and task capabilities; editor connectivity is queried separately."),
    tool("godot_tools_discover", "Find stateful tools on demand. Returns compact summaries by default; request include_schema=true for selected tools. Prefer ordinary file editing for saved code and assets.", {"query": {"type": "string"}}),
    tool("godot_tools_call", "Invoke a registered tool with the same validation and execution path.", {"tool_name": STRING, "arguments": {"type": "object"}}, ["tool_name", "arguments"]),
    tool("godot_editor_status", "Read editor state, reporting unavailable connection explicitly."),
    tool("godot_diagnostics_read", "Read editor diagnostics or a registered task output.", {"job_id": STRING, "cursor": {"type": "integer", "minimum": 0}, "max_entries": {"type": "integer", "minimum": 1, "maximum": 2048}, "levels": {"type": "array", "maxItems": 5, "items": {"type": "string", "enum": ["info", "error", "warning", "editor"]}}}),
    tool("godot_job_get", "Read an editor/run operation and its result or failure.", {"job_id": STRING}, ["job_id"]),
    tool("godot_job_wait", "Wait on a task condition notification; wait expiry does not cancel the task.", {"job_id": STRING, "timeout_ms": {"type": "integer", "minimum": 1, "maximum": 30000}}, ["job_id", "timeout_ms"]),
    tool("godot_job_cancel", "Request cancellation of an editor operation; already committed operations report rejection.", {"job_id": STRING}, ["job_id"]),
    tool("godot_events_read", "Read bounded task events, optionally waiting for a notification.", {"cursor": {"type": "integer", "minimum": 0}, "wait_ms": {"type": "integer", "minimum": 0, "maximum": 30000}}),
    tool("godot_artifact_read", "Read a bounded slice of registered task output.", {"artifact_id": STRING, "offset": {"type": "integer", "minimum": 0}, "length": {"type": "integer", "minimum": 1, "maximum": 1048576}}, ["artifact_id"]),
]

LOCAL_TOOLS.extend([
    tool("godot_resource_reimport", "Reimport assets in the current editor as a tracked operation, verifying each loaded result.", {"paths": {"type": "array", "minItems": 1, "maxItems": 256, "items": STRING}}, ["paths"]),
    tool("godot_editor_start", "Reuse or start a project editor and wait for its authenticated endpoint.", {"reuse": {"type": "boolean"}, "display_mode": {"type": "string", "enum": ["headless_logic", "visible"]}, "timeout_ms": TIMEOUT}),
    tool("godot_editor_close", "Preflight versioned unsaved documents and wait for actual editor process exit.", {"editor_epoch": STRING, "expected_document_revision": STRING, "unsaved": {"type": "string", "enum": ["reject", "save", "discard"]}, "timeout_ms": TIMEOUT}, ["editor_epoch", "expected_document_revision"]),
    tool("godot_editor_restart", "Close and restart the editor, restoring saved scene documents.", {"editor_epoch": STRING, "expected_document_revision": STRING, "unsaved": {"type": "string", "enum": ["reject", "save", "discard"]}, "display_mode": {"type": "string", "enum": ["headless_logic", "visible"]}, "timeout_ms": TIMEOUT}, ["editor_epoch", "expected_document_revision"]),
])


LOCAL_TOOLS.extend([
    tool("godot_run_start", "Start an owned isolated game process and wait for its exact PID and tree_ready handshake.", {"rendering_method": {"type": "string", "enum": ["mobile", "forward_plus", "gl_compatibility"]}, "rendering_driver": {"type": "string", "enum": ["metal", "vulkan", "d3d12", "opengl3"]}, "scene": STRING, "display_mode": {"type": "string", "enum": ["headless_logic", "hidden_gpu", "visible"]}, "arguments": {"type": "array", "maxItems": 64, "items": {"type": "string"}}, "timeout_ms": TIMEOUT}),
    tool("godot_run_stop", "Stop an owned run by identity and wait for actual process exit.", {"run_id": STRING, "timeout_ms": TIMEOUT}, ["run_id"]),
    tool("godot_run_list", "List authenticated debug sessions and sidecar-owned runs.", {"include_stopped": {"type": "boolean"}}),
    tool("godot_run_wait", "Wait for an exact run stage; headless rendering never reports first_render.", {"run_id": STRING, "stage": {"type": "string", "enum": ["connected", "tree_ready", "first_physics", "first_render", "exited"]}, "minimum_sessions": {"type": "integer", "minimum": 1, "maximum": 16}, "timeout_ms": TIMEOUT}),
])


LOCAL_TOOLS.extend([
    tool("godot_capture_frame", "Capture real game, viewport or current-camera GPU pixels into a PNG artifact.", {"session_id": STRING, "target": {"type": "string", "enum": ["game", "viewport", "camera"]}, "object_ref": STRING, "node_path": STRING, "width": {"type": "integer", "minimum": 1, "maximum": 2048}, "height": {"type": "integer", "minimum": 1, "maximum": 2048}}, ["session_id"]),
])




class Services:
    TERMINAL = {"succeeded", "failed", "cancelled", "timed_out"}

    def __init__(self, project, executable=None, toolset="core"):
        self.project = Path(project).resolve() if project else None
        self.service_id = uuid.uuid4().hex
        self.toolset = toolset
        self.executable = executable or os.environ.get("GODOT_MCP_EXECUTABLE")
        if os.name == "nt" and self.executable and str(self.executable).lower().endswith(".console.exe"):
            gui_executable = Path(str(self.executable)[:-len(".console.exe")] + ".exe")
            if not gui_executable.is_file():
                raise ValueError("The Godot console launcher requires its matching editor executable")
            self.executable = gui_executable
        self.condition = threading.Condition(threading.RLock())
        self.jobs = {}
        self.artifacts = {}
        self.events = deque(maxlen=2048)
        self.sequence = 0
        self.storage = tempfile.TemporaryDirectory(prefix="godot-mcp-jobs-")
        self.closed = False
        self.lifecycle = Lifecycle(self)
        self.runs = Runs(self)
        self.package = source_identity()

    def emit_event(self, kind, **fields):
        with self.condition:
            self.sequence += 1
            self.events.append({"sequence": self.sequence, "kind": kind, **fields})
            self.condition.notify_all()
            return self.sequence

    def _event(self, job):
        job["sequence"] = self.emit_event("job_state", job_id=job["job_id"], state=job["state"], operation=job["kind"])

    def snapshot(self, job):
        return {k: v for k, v in job.items() if k not in {"process", "thread", "output_path", "report_path", "json_report", "result_artifacts", "require_artifacts"}}

    def invoke(self, name, args, catalog, dispatch, editor):
        if name == "godot_extension_reload":
            def reload_extension():
                ticket = editor("_extension_reload_begin", args)
                deadline = time.monotonic() + 9
                while True:
                    # Each RPC waits for the editor event loop over the socket.
                    # Deferred script reload must finish before validation.
                    result = editor("_extension_reload_complete", ticket)
                    if not result.get("pending", False):
                        return result
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Native reload completion timed out; inspect editor state and do not replay automatically")
            return self.submit_operation(name, reload_extension)
        if name in {"godot_run_start", "godot_run_list", "godot_run_stop"} or (name == "godot_run_wait" and "run_id" in args):
            return self.runs.invoke(name, args, editor)
        if name == "godot_run_wait":
            return editor(name, args)
        if name == "godot_resource_reimport":
            def reimport():
                result = editor(name, args)
                if not result.get("success"):
                    raise RuntimeError("IMPORT_FAILED: " + json.dumps(result, ensure_ascii=False))
                return result
            return self.submit_operation(name, reimport)
        if name == "godot_capture_frame":
            result = editor(name, args)
            data = base64.b64decode(result.pop("png_base64"), validate=True)
            if len(data) > 3145728 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("Invalid or oversized capture")
            path = Path(self.storage.name) / (uuid.uuid4().hex + ".png")
            path.write_bytes(data)
            return {**result, "artifact_id": self.register_artifact(path, "image/png"), "_mcp_image_base64": base64.b64encode(data).decode("ascii")}
        if name == "godot_tools_discover":
            query = args.get("query", "").lower()
            matches = [t for t in catalog if query in (t["name"] + " " + t["description"]).lower() and ("domains" not in args or t["_meta"]["godot/domain"] in args["domains"]) and ("tier" not in args or t["_meta"]["godot/tier"] == args["tier"])]
            offset = args.get("offset", 0)
            if offset > len(matches):
                raise ValueError("Discovery offset exceeds filtered tool count")
            page = matches[offset:offset + args.get("limit", 20)]
            if not args.get("include_schema", False):
                page = [{key: item[key] for key in ["name", "description", "_meta"]} for item in page]
            return {"tools": page, "next_offset": offset + len(page), "total": len(matches), "has_more": offset + len(page) < len(matches)}
        if name == "godot_tools_call":
            if args["tool_name"] == name:
                raise ValueError("Recursive tool dispatch is not supported")
            return dispatch(args["tool_name"], args["arguments"])
        if name == "godot_capabilities_get":
            try:
                state = editor("_editor_state", {})
                connected = {"connected": True, "version": state.get("engine_build"), "native_api": state.get("native_api", {}), "package": state.get("package", {}), "editor_epoch": state.get("editor_epoch")}
            except (OSError, ValueError, RuntimeError) as error:
                connected = {"connected": False, "reason": str(error)}
            return {"tool_names": [t["name"] for t in catalog], "default_tool_names": [t["name"] for t in catalog if t["name"] in CORE_TOOL_NAMES], "active_toolset": self.toolset, "workflow": "file_first", "protocol": PROTOCOL, "version": VERSION, "package": {key: value for key, value in self.package.items() if key != "files"}, "editor": connected, "schema_sha256": hashlib.sha256(json.dumps(catalog, sort_keys=True, ensure_ascii=False).encode()).hexdigest(), "providers": sorted({t["_meta"]["godot/provider"] for t in catalog}), "configured_executable": str(self.executable) if self.executable else None, "execution_modes": {"editor": ["headless_logic", "visible"], "runtime": ["headless_logic", "hidden_gpu", "visible"]}, "platform_limits": ["hidden_gpu requires a working graphical login session and supported renderer", "debugger step/variables depend on the script language; GDScript validated", "only sidecar-owned runs can be stopped", "isolated pose seeks one clip before modifiers; live pose reads current state"], "task_storage": "service_lifetime", "external_workflows": ["script_execution", "disk_script_validation", "test_runners", "baking", "export", "image_comparison"], "project_path": str(self.project) if self.project else None}
        if name == "godot_editor_status":
            try:
                return {"connected": True, "state": editor("_editor_state", {})}
            except (OSError, ValueError, RuntimeError) as error:
                return {"connected": False, "reason": str(error), "startup_job": self.snapshot(self.jobs[self.startup_job]) if getattr(self, "startup_job", None) else None}
        if name == "godot_diagnostics_read" and "job_id" not in args:
            return editor("_editor_logs", args)
        if name in {"godot_editor_start", "godot_editor_close", "godot_editor_restart"}:
            action = {"godot_editor_start": self.lifecycle.start, "godot_editor_close": self.lifecycle.close, "godot_editor_restart": self.lifecycle.restart}[name]
            return self.submit_operation(name, lambda: action(args, editor))
        with self.condition:
            if name == "godot_events_read":
                cursor = args.get("cursor", 0)
                if cursor > self.sequence:
                    raise ValueError("Event cursor is beyond the latest sequence")
                def matches(event):
                    return event["sequence"] > cursor and ("job_id" not in args or event.get("job_id") == args["job_id"]) and ("session_id" not in args or event.get("session_id") == args["session_id"]) and ("kinds" not in args or event["kind"] in args["kinds"])
                self.condition.wait_for(lambda: any(matches(event) for event in self.events) or self.closed, args.get("wait_ms", 0) / 1000)
                entries = [e.copy() for e in self.events if matches(e)]
                oldest = self.events[0]["sequence"] if self.events else self.sequence + 1
                return {"events": entries, "next_cursor": self.sequence, "dropped": cursor + 1 < oldest}
            if name == "godot_artifact_read":
                artifact_id = args["artifact_id"]
                if artifact_id in self.artifacts:
                    artifact = self.artifacts[artifact_id]
                    path = Path(artifact["path"])
                    complete = True
                    mime_type = artifact["mime_type"]
                else:
                    job_id = artifact_id.removesuffix(":output")
                    if artifact_id != job_id + ":output" or job_id not in self.jobs:
                        raise ValueError("Unknown artifact")
                    job = self.jobs[job_id]
                    path = Path(job["output_path"])
                    complete = job["state"] in self.TERMINAL
                    mime_type = "text/plain"
                if args.get("offset", 0) > path.stat().st_size:
                    raise ValueError("Artifact offset is beyond the end of the file")
                with path.open("rb") as stream:
                    stream.seek(args.get("offset", 0))
                    data = stream.read(args.get("length", 65536))
                return {"artifact_id": artifact_id, "mime_type": mime_type, "encoding": "base64", "data": base64.b64encode(data).decode(), "next_offset": args.get("offset", 0) + len(data), "size": path.stat().st_size, "complete": complete, "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if complete else None, "storage": "service_lifetime"}
            job = self.jobs.get(args["job_id"])
            if job is None:
                raise ValueError("Unknown job")
            if name == "godot_job_wait":
                self.condition.wait_for(lambda: job["state"] in self.TERMINAL or self.closed or (args.get("until") == "changed" and job["sequence"] > args["after_sequence"]), args["timeout_ms"] / 1000)
            elif name == "godot_job_cancel" and job["state"] not in self.TERMINAL:
                if job["state"] == "queued":
                    job["cancel_requested"] = True
                    job["state"] = "cancelled"
                    self._event(job)
                    return {**self.snapshot(job), "cancellation_accepted": True, "side_effects_applied": False}
                if job.get("cancellable") is False:
                    return {**self.snapshot(job), "cancellation_accepted": False, "reason": "Operation already committed or completing"}
            elif name == "godot_job_cancel":
                return {**self.snapshot(job), "cancellation_accepted": False, "reason": "Operation is already terminal"}
            elif name == "godot_diagnostics_read":
                return {"artifacts": job["artifacts"], "state": job["state"], "exit_code": job.get("exit_code")}
            return self.snapshot(job)

    def register_artifact(self, path, mime_type):
        path = Path(path).resolve()
        if not path.is_relative_to(Path(self.storage.name).resolve()) or not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("Artifact must be a bounded service-owned file")
        with self.condition:
            if len(self.artifacts) >= 512:
                raise ValueError("Artifact budget exhausted")
            artifact_id = uuid.uuid4().hex
            self.artifacts[artifact_id] = {"path": str(path), "mime_type": mime_type}
            return artifact_id

    def submit_operation(self, kind, action):
        with self.condition:
            if self.closed or len(self.jobs) >= 128:
                raise ValueError("Task service closed or job budget exhausted")
            if sum(job["state"] not in self.TERMINAL for job in self.jobs.values()) >= 8:
                raise ValueError("Active operation budget exhausted; request was not submitted")
            job_id = uuid.uuid4().hex
            output = Path(self.storage.name) / job_id
            output.touch()
            job = {"job_id": job_id, "kind": kind, "state": "queued", "process": None, "output_path": str(output), "artifacts": [job_id + ":output"], "cancel_requested": False, "cancellable": False}
            self.jobs[job_id] = job
            def run():
                with self.condition:
                    if job["cancel_requested"] or self.closed:
                        job["state"] = "cancelled"
                        self._event(job)
                        return
                    job["state"] = "running"
                    self._event(job)
                try:
                    result = action()
                    with self.condition:
                        Path(job["output_path"]).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
                        job["result"] = result
                        job["state"] = "succeeded"
                        self._event(job)
                except Exception as error:
                    with self.condition:
                        Path(job["output_path"]).write_text(str(error), encoding="utf-8")
                        job["error"] = str(error)
                        job["state"] = "timed_out" if isinstance(error, TimeoutError) else "failed"
                        self._event(job)
            worker = threading.Thread(target=run, daemon=True)
            job["thread"] = worker
            self._event(job)
            worker.start()
            return self.snapshot(job)

    def begin_shutdown(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()

    def close(self):
        self.begin_shutdown()
        with self.condition:
            workers = [job["thread"] for job in self.jobs.values()]
            self.condition.notify_all()
        for worker in workers:
            worker.join()
        self.runs.close()
        self.storage.cleanup()
