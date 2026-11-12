"""Providers register atomically and all schemas/calls share one catalog."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sidecar"))
from tool_registry import ToolRegistry


class RegistryTests(unittest.TestCase):
    def test_provider_is_copied_callable_and_frozen(self):
        registry = ToolRegistry()
        tool = {"name": "godot_probe_read", "description": "Probe", "inputSchema": {"type": "object"}, "outputSchema": {"type": "object"}}
        registry.register_provider("probe", [tool], lambda name, args, context: {"ok": True, "result": {"context": context}})
        tool["description"] = "Changed outside registry"
        self.assertEqual(registry.tools[0]["description"], "Probe")
        self.assertEqual(registry.invoke("godot_probe_read", {}, "fixture")["result"]["context"], "fixture")
        self.assertEqual(registry.tools[0]["_meta"]["godot/provider"], "probe")
        with self.assertRaises(ValueError):
            registry.register_provider("duplicate", [tool], lambda *_: None)
        self.assertEqual(len(registry.tools), 1)
        registry.freeze()
        with self.assertRaises(ValueError):
            registry.register_provider("late", [], lambda *_: None)


if __name__ == "__main__":
    unittest.main()
