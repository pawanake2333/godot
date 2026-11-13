"""Owned game processes attached to the existing editor debugger."""
from __future__ import annotations

from pathlib import Path
import subprocess
import os
import threading
import uuid


class Runs:
    def __init__(self, service):
        self.service = service
        self.records = {}
        self.lock = threading.RLock()
        self.job = None
        if os.name == "nt":
            from windows_job import WindowsJob
            self.job = WindowsJob()

    def start(self, args, editor):
        service = self.service
        if service.project is None or not service.executable:
            raise ValueError("Configure --project and --godot")
        prepared = editor("godot_debugger_prepare", {})
        scene = args.get("scene", "main")
        if scene == "current":
            scene = editor("_editor_state", {}).get("edited_scene", "")
            if not scene:
                raise ValueError("Current scene must be saved")
        if scene != "main":
            path = (service.project / scene.removeprefix("res://")).resolve()
            if not scene.startswith("res://") or not path.is_relative_to(service.project) or not path.is_file():
                raise ValueError("Scene must be an existing res:// project scene")
        mode = args.get("display_mode", "headless_logic")
        command = [str(service.executable), *(["--headless"] if mode == "headless_logic" else ["--hidden"] if mode == "hidden_gpu" else []), "--path", str(service.project), "--remote-debug", prepared["debugger_uri"]]
        if "rendering_method" in args:
            command.extend(["--rendering-method", args["rendering_method"]])
        if "rendering_driver" in args:
            command.extend(["--rendering-driver", args["rendering_driver"]])
        if scene != "main":
            command.append(scene)
        command.extend(["--", *args.get("arguments", [])])
        with self.lock:
            if len(self.records) >= 64:
                raise ValueError("Run record budget exhausted")
            run_id = uuid.uuid4().hex
            output = Path(service.storage.name) / ("run-" + run_id + ".log")
            with output.open("wb") as stream:
                process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                if self.job:
                    self.job.assign(process)
            except Exception:
                process.kill()
                process.wait()
                raise
            record = {"run_id": run_id, "pid": process.pid, "display_mode": mode, "scene": scene, "owned": True, "process": process, "output_path": str(output)}
            self.records[run_id] = record
        try:
            ready = editor("godot_run_wait", {"pid": process.pid, "stage": "tree_ready", "timeout_ms": min(args.get("timeout_ms", 9000), 9000)})
            record["session_id"] = ready["session"]["session_id"]
            service.emit_event("run_stage", run_id=run_id, session_id=record["session_id"], stage="tree_ready", pid=process.pid)
            return {**self.snapshot(record), "session": ready["session"]}
        except Exception as error:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)
            raise RuntimeError(f"Run failed before tree_ready: {error}; {output.read_text(errors='replace')[-8192:]}") from error

    def snapshot(self, record):
        return {**{k: v for k, v in record.items() if k not in {"process", "output_path"}}, "exit_code": record["process"].poll(), "state": "running" if record["process"].poll() is None else "exited"}

    def invoke(self, name, args, editor):
        if name == "godot_run_start":
            return self.service.submit_operation(name, lambda: self.start(args, editor))
        if name == "godot_run_list":
            sessions = editor(name, {})
            with self.lock:
                sessions["runs"] = [self.snapshot(record) for record in self.records.values() if args.get("include_stopped", False) or record["process"].poll() is None]
            return sessions
        with self.lock:
            record = self.records.get(args.get("run_id"))
        if record is None:
            raise ValueError("Unknown run_id; only runs owned by this sidecar can be controlled")
        if name == "godot_run_stop":
            def stop():
                process = record["process"]
                if process.poll() is None:
                    try:
                        editor("godot_runtime_stop", {"session_id": record["session_id"]})
                    except (OSError, ValueError, RuntimeError) as error:
                        record["graceful_request_error"] = str(error)
                    try:
                        process.wait(timeout=args.get("timeout_ms", 5000) / 1000)
                    except subprocess.TimeoutExpired:
                        record["stop_method"] = "terminate"
                        process.terminate()
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            record["stop_method"] = "kill"
                            process.kill()
                            process.wait(timeout=5)
                self.service.emit_event("run_exited", run_id=record["run_id"], session_id=record.get("session_id"), exit_code=process.returncode)
                return self.snapshot(record)
            return self.service.submit_operation(name, stop)
        if args.get("stage") == "exited":
            try:
                record["process"].wait(timeout=args.get("timeout_ms", 5000) / 1000)
            except subprocess.TimeoutExpired:
                return {**self.snapshot(record), "reached": False, "stage": "exited"}
            return {**self.snapshot(record), "reached": True, "stage": "exited"}
        if record["process"].poll() is not None:
            raise RuntimeError("Run exited before requested stage")
        result = editor(name, {"session_id": record["session_id"], "stage": args.get("stage", "tree_ready"), "timeout_ms": min(args.get("timeout_ms", 5000), 9000)})
        self.service.emit_event("run_stage", run_id=record["run_id"], session_id=record["session_id"], stage=result["stage"])
        return result

    def close(self):
        with self.lock:
            for record in self.records.values():
                process = record["process"]
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
        if self.job:
            self.job.close()
