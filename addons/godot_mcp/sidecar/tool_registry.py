"""Startup-only provider registration shared by list, discover and dispatch."""
from copy import deepcopy
import re
from tool_policy import CORE_TOOL_NAMES


class ToolRegistry:
    def __init__(self):
        self.tools = []
        self._by_name = {}
        self._providers = {}
        self._frozen = False

    def register_provider(self, name, definitions, handler):
        if self._frozen:
            raise ValueError("Providers must be registered before MCP initialization")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name) or name in self._providers or not callable(handler):
            raise ValueError("Provider requires a unique name and callable handler")
        entries = deepcopy(definitions)
        names = [entry.get("name") for entry in entries]
        if len(self.tools) + len(entries) > 256 or len(names) != len(set(names)):
            raise ValueError("Duplicate tool or registry budget exceeded")
        for entry in entries:
            tool_name = entry.get("name", "")
            if not re.fullmatch(r"godot_[a-z][a-z0-9_]{0,95}", tool_name) or tool_name in self._by_name:
                raise ValueError("Invalid or duplicate tool name")
            if not isinstance(entry.get("description"), str):
                raise ValueError("Tool description is required")
            for schema_name in ["inputSchema", "outputSchema"]:
                if not isinstance(entry.get(schema_name), dict) or entry[schema_name].get("type") != "object":
                    raise ValueError("Provider tools require object input and output schemas")
        # Publish only after the entire provider passes validation.
        self._providers[name] = handler
        for entry in entries:
            entry.setdefault("_meta", {}).update({"godot/provider": name, "godot/domain": entry["name"].removeprefix("godot_").split("_", 1)[0], "godot/tier": "core" if entry["name"] in CORE_TOOL_NAMES else "extended"})
            self._by_name[entry["name"]] = (entry, handler)
            self.tools.append(entry)

    def freeze(self):
        self._frozen = True

    def invoke(self, name, arguments, context):
        if name not in self._by_name:
            raise ValueError("Unknown registered tool")
        return self._by_name[name][1](name, arguments, context)
