"""Multiplex request IDs over one authenticated editor/input connection."""
from concurrent.futures import Future
import json
import queue
import socket
import threading


class RPCConnection:
    def __init__(self, sock, stream, on_disconnect, on_context):
        self.sock = sock
        self.stream = stream
        self.on_disconnect = on_disconnect
        self.on_context = on_context
        self.lock = threading.RLock()
        self.pending = {}
        self.outbound = queue.Queue(maxsize=32)
        self.closed = False
        self.first_written = threading.Event()
        self.writer = threading.Thread(target=self._write, daemon=True)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.writer.start()
        self.reader.start()

    def request(self, payload, timeout):
        request_id = payload["id"]
        future = Future()
        with self.lock:
            if self.closed:
                raise ConnectionError("Editor connection closed; request was not submitted")
            if len(self.pending) >= 32:
                raise ValueError("Editor request budget exhausted")
            self.pending[request_id] = future
            self.outbound.put_nowait((json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode())
        try:
            return future.result(timeout=timeout)
        except TimeoutError as error:
            self.close(TimeoutError("Editor request deadline; outcome may already be applied; no request is replayed"))
            raise TimeoutError("Editor request deadline; inspect current state before retrying a mutation") from error

    def _write(self):
        try:
            while True:
                payload = self.outbound.get()
                if payload is None:
                    return
                if self.closed:
                    return
                self.stream.write(payload)
                self.stream.flush()
                self.first_written.set()
        except Exception as error:
            self.close(error)

    def _read(self):
        self.first_written.wait()
        try:
            while not self.closed:
                line = self.stream.readline(8 * 1024 * 1024 + 1)
                if not line:
                    raise ConnectionError("Godot editor bridge closed the connection")
                if len(line) > 8 * 1024 * 1024:
                    raise RuntimeError("Godot editor response exceeded 8 MiB")
                response = json.loads(line)
                with self.lock:
                    future = self.pending.pop(response.get("id"), None)
                    if future is None:
                        if self.closed:
                            return
                        raise RuntimeError("Unmatched editor response id")
                self.on_context(response.get("context", {}))
                future.set_result(response)
        except Exception as error:
            self.close(error)

    def close(self, error=None):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            pending = list(self.pending.values())
            self.pending.clear()
            while True:
                try:
                    self.outbound.get_nowait()
                except queue.Empty:
                    break
            self.outbound.put_nowait(None)
            self.first_written.set()
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.stream.close()
        self.sock.close()
        self.on_disconnect(self)
        for future in pending:
            if not future.done():
                future.set_exception(error or ConnectionError("Editor connection closed; request outcome is unknown"))
