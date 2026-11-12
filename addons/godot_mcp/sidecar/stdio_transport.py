"""Bounded concurrent MCP dispatch; control queries remain available during waits."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading


def serve(stdin, stdout, handle, rpc_error, shutdown):
    output_lock = threading.Lock()
    capacity = threading.BoundedSemaphore(64)
    work = ThreadPoolExecutor(max_workers=6, thread_name_prefix="mcp-tool")
    control = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mcp-control")

    def emit(response):
        if response is None:
            return
        encoded = json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n"
        with output_lock:
            stdout.write(encoded)
            stdout.flush()

    def execute(message):
        try:
            emit(handle(message))
        except Exception as error:
            emit(rpc_error(message.get("id"), -32603, "Internal error", str(error)))
        finally:
            capacity.release()

    try:
        while True:
            line = stdin.readline(4 * 1024 * 1024 + 1)
            if not line:
                break
            if len(line) > 4 * 1024 * 1024:
                while line and not line.endswith("\n"):
                    line = stdin.readline(65536)
                emit(rpc_error(None, -32700, "Request exceeds four MiB"))
                continue
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as error:
                emit(rpc_error(None, -32700, "Parse error", str(error)))
                continue
            if not isinstance(message, dict):
                emit(rpc_error(None, -32600, "Expected a JSON-RPC object"))
                continue
            if not capacity.acquire(blocking=False):
                emit(rpc_error(message.get("id"), -32000, "Request queue is full; request was not submitted"))
                continue
            params = message.get("params") or {}
            pure = message.get("method") in {"ping", "tools/list"} or (isinstance(params, dict) and params.get("name") in {"godot_capabilities_get", "godot_tools_discover", "godot_job_get", "godot_job_cancel", "godot_artifact_read"})
            (control if pure else work).submit(execute, message)
    finally:
        shutdown()
        work.shutdown(wait=True, cancel_futures=True)
        control.shutdown(wait=True, cancel_futures=True)
