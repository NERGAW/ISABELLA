"""Separate MCP tool catalog and mapping into local Skills."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from Isabella.Skills.base import ParameterSpec, SkillDefinition, SkillResult
from .models import MCPTool


JSON_TYPES = {
    "string": str, "integer": int, "number": (int, float),
    "boolean": bool, "array": list, "object": dict,
}


class MCPToolRegistry:
    def __init__(self, skill_registry=None) -> None:
        self.skill_registry = skill_registry
        self._tools: dict[str, MCPTool] = {}

    def register(self, tool: MCPTool, executor: Callable[[MCPTool, dict[str, Any]], SkillResult]) -> SkillDefinition:
        if tool.skill_id in self._tools:
            raise ValueError(f"MCP tool already registered: {tool.skill_id}")
        schema = tool.input_schema or {}
        required = set(schema.get("required", []))
        parameters = {
            name: ParameterSpec(JSON_TYPES.get(spec.get("type"), object), name in required)
            for name, spec in schema.get("properties", {}).items()
        }
        definition = SkillDefinition(
            tool.skill_id, tool.name, tool.description, "mcp", parameters,
            tool.risk_level, lambda arguments, item=tool: executor(item, arguments),
        )
        self._tools[tool.skill_id] = tool
        if self.skill_registry:
            self.skill_registry.register(definition)
        return definition

    def unregister_server(self, server_id: str) -> None:
        ids = [skill_id for skill_id, tool in self._tools.items() if tool.server_id == server_id]
        for skill_id in ids:
            self._tools.pop(skill_id, None)
            if self.skill_registry:
                self.skill_registry.unregister(skill_id)

    def get(self, skill_id: str) -> MCPTool | None:
        return self._tools.get(skill_id)

    def list(self, server_id: str | None = None) -> list[MCPTool]:
        return [tool for tool in self._tools.values() if server_id is None or tool.server_id == server_id]

