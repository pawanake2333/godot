"""Two editor requests must complete out of order on the same TCP identity."""
import json
from pathlib import Path
import socket
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sidecar"))
import godot_mcp_server as server


class MultiplexTests(unittest.TestCase):
    def test_responses_are_routed_by_id_without_serial_waits(self):
        client, peer = socket.socketpair()
        peer.settimeout(3)
        stream = peer.makefile("rwb")
        bridge = server.GodotBridge("127.0.0.1", 12345, 1, 3)
        def respond():
            requests = [json.loads(stream.readline()), json.loads(stream.readline())]
            for request in reversed(requests):
                stream.write(json.dumps({"id": request["id"], "ok": True, "result": {"tool": request["params"]["tool"]}}).encode() + b"\n")
                stream.flush()
        try:
            with ThreadPoolExecutor(max_workers=3) as executor:
                responder = executor.submit(respond)
                with patch.object(server.socket, "create_connection", return_value=client) as connect:
                    first = executor.submit(bridge.invoke, "first", {})
                    second = executor.submit(bridge.invoke, "second", {})
                    self.assertEqual(first.result(timeout=4)["result"]["tool"], "first")
                    self.assertEqual(second.result(timeout=4)["result"]["tool"], "second")
                    connect.assert_called_once()
                responder.result(timeout=4)
        finally:
            bridge.close()
            stream.close()
            peer.close()


if __name__ == "__main__":
    unittest.main()
