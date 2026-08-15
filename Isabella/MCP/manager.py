"""Secure MCP server lifecycle and tool execution coordinator."""

from __future__ import annotations

from collections import deque
import json
import logging
from pathlib import Path
import threading
from time import perf_counter
from typing import Any, Callable

from Isabella.Core.config import ConfigurationError, PROJECT_ROOT
from Isabella.Events import EventPriority, EventType
from Isabella.Skills.base import SkillResult
from .client import MCPClient, MCPClientError
from .models import MCPServer, MCPServerState, MCPTool, MCPTransport
from .registry import MCPToolRegistry


LOGGER = logging.getLogger("MCP")
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "mcp.json"
SECRET_WORDS = ("token", "secret", "password", "api_key", "private_key", "credential", "authorization")


def _contains_secret_value(value: Any, key: str = "") -> bool:
    if key in {"environment_variables", "headers_from_environment"}:
        return not isinstance(value, dict) or any(
            not isinstance(item, str) or not item or not item.replace("_", "A").isalnum() or item.upper() != item
            for item in value.values()
        )
    if isinstance(value, dict):
        return any(_contains_secret_value(item, str(name)) for name, item in value.items())
    return any(word in key.casefold() for word in SECRET_WORDS) and key != "environment_variables"


def load_mcp_config(path: Path | None = None) -> dict[str, Any]:
    target = path or DEFAULT_CONFIG_PATH
    try:
        config = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Invalid MCP configuration: {target}") from exc
    required = {"enabled", "servers", "connection_timeout_seconds", "tool_timeout_seconds", "auto_connect"}
    if not isinstance(config, dict) or required - config.keys() or not isinstance(config["servers"], list):
        raise ConfigurationError("MCP configuration is missing required fields")
    if not 0.1 <= float(config["connection_timeout_seconds"]) <= 120:
        raise ConfigurationError("MCP connection timeout is invalid")
    if not 0.1 <= float(config["tool_timeout_seconds"]) <= 300:
        raise ConfigurationError("MCP tool timeout is invalid")
    if _contains_secret_value(config):
        raise ConfigurationError("Secrets are not allowed in MCP configuration")
    return config


class MCPManager:
    def __init__(
        self, config: dict[str, Any], *, skill_registry=None, event_bus=None,
        client_factory: Callable[[MCPServer], Any] = MCPClient,
    ) -> None:
        self.config = config
        self.enabled = bool(config["enabled"])
        self.auto_connect = bool(config["auto_connect"])
        self.connection_timeout = float(config["connection_timeout_seconds"])
        self.tool_timeout = float(config["tool_timeout_seconds"])
        self.event_bus = event_bus
        self.tool_registry = MCPToolRegistry(skill_registry)
        self._client_factory = client_factory
        self._servers: dict[str, MCPServer] = {}
        self._clients: dict[str, Any] = {}
        self._states: dict[str, MCPServerState] = {}
        self.recent_failures: deque[dict[str, str]] = deque(maxlen=50)
        self.call_latencies_ms: deque[float] = deque(maxlen=200)
        self._lock = threading.RLock()
        for server_data in config["servers"]:
            self.register_server(self._server_from_dict(server_data))

    @classmethod
    def from_config(cls, path: Path | None = None, **components) -> "MCPManager":
        return cls(load_mcp_config(path), **components)

    @staticmethod
    def _server_from_dict(data: dict[str, Any]) -> MCPServer:
        location = data.get("command") or data.get("url")
        return MCPServer(
            id=data["id"], name=data["name"], transport=MCPTransport(data["transport"]),
            command_or_url=location, enabled=bool(data.get("enabled", True)),
            trusted=bool(data.get("trusted", False)), timeout=float(data.get("timeout", 30)),
            metadata=dict(data.get("metadata", {})),
        )

    def register_server(self, server: MCPServer | dict[str, Any]) -> MCPServer:
        item = self._server_from_dict(server) if isinstance(server, dict) else server
        with self._lock:
            if item.id in self._servers:
                raise ValueError(f"MCP server already registered: {item.id}")
            self._servers[item.id] = item
            self._states[item.id] = MCPServerState.DISCONNECTED
        return item

    def start(self) -> bool:
        if not self.enabled:
            return True
        if self.auto_connect:
            for server in self.list_servers():
                if server.enabled:
                    self.connect(server.id)
        return True

    def connect(self, server_id: str) -> bool:
        server = self._require_server(server_id)
        if not self.enabled or not server.enabled:
            return False
        with self._lock:
            if self._states[server_id] is MCPServerState.CONNECTED:
                return True
            self._states[server_id] = MCPServerState.CONNECTING
        client = self._client_factory(server)
        try:
            tools = client.connect(min(self.connection_timeout, server.timeout))
            for tool in tools:
                self.tool_registry.register(tool, self._execute_tool)
            with self._lock:
                self._clients[server_id] = client
                self._states[server_id] = MCPServerState.CONNECTED
            self._emit(EventType.MCP_SERVER_CONNECTED, {"server_id": server_id, "tools": len(tools)})
            return True
        except Exception as exc:
            self.tool_registry.unregister_server(server_id)
            try:
                client.disconnect()
            except Exception:
                pass
            self._failure(server_id, None, exc)
            with self._lock:
                self._states[server_id] = MCPServerState.ERROR
            return False

    def disconnect(self, server_id: str) -> bool:
        self._require_server(server_id)
        with self._lock:
            client = self._clients.pop(server_id, None)
        stopped = True if client is None else bool(client.disconnect())
        self.tool_registry.unregister_server(server_id)
        with self._lock:
            self._states[server_id] = MCPServerState.DISCONNECTED if stopped else MCPServerState.ERROR
        if stopped:
            self._emit(EventType.MCP_SERVER_DISCONNECTED, {"server_id": server_id})
        return stopped

    def list_servers(self) -> list[MCPServer]:
        with self._lock:
            return list(self._servers.values())

    def list_tools(self, server_id: str | None = None) -> list[MCPTool]:
        return self.tool_registry.list(server_id)

    def call_tool(
        self, skill_id: str, arguments: dict[str, Any], *, source_request_id: str = "mcp-direct",
        confirmation_id: str | None = None, confirmation_source: str = "untrusted",
    ) -> SkillResult:
        registry = self.tool_registry.skill_registry
        if registry is None:
            return SkillResult(False, skill_id, "Registro local de segurança indisponível.", error_code="SECURITY_UNAVAILABLE", status="denied")
        return registry.execute(
            skill_id, arguments, source_request_id=source_request_id,
            confirmation_id=confirmation_id, confirmation_source=confirmation_source,
        )

    def _execute_tool(self, tool: MCPTool, arguments: dict[str, Any]) -> SkillResult:
        started = perf_counter()
        self._emit(EventType.MCP_TOOL_STARTED, {"server_id": tool.server_id, "tool_id": tool.skill_id})
        try:
            client = self._clients.get(tool.server_id)
            if client is None:
                raise MCPClientError("MCP server is not connected")
            result = client.call_tool(tool.name, arguments, min(self.tool_timeout, self._servers[tool.server_id].timeout))
            if not result.success:
                raise MCPClientError(result.error or "MCP tool failed")
            response = SkillResult(True, tool.skill_id, "Ferramenta MCP executada.", {"content": result.content})
            self._emit(EventType.MCP_TOOL_COMPLETED, {"server_id": tool.server_id, "tool_id": tool.skill_id})
            return response
        except Exception as exc:
            self._failure(tool.server_id, tool.skill_id, exc)
            self._emit(EventType.MCP_TOOL_FAILED, {"server_id": tool.server_id, "tool_id": tool.skill_id, "error": type(exc).__name__}, high=True)
            return SkillResult(False, tool.skill_id, "A ferramenta MCP falhou.", error_code="MCP_TOOL_ERROR", status="failed")
        finally:
            self.call_latencies_ms.append((perf_counter() - started) * 1000)

    def health_check(self) -> dict[str, Any]:
        with self._lock:
            connected = [server_id for server_id, state in self._states.items() if state is MCPServerState.CONNECTED]
            unhealthy = [
                server_id for server_id, state in self._states.items()
                if state is MCPServerState.ERROR or (
                    state is MCPServerState.CONNECTED and not self._clients[server_id].health_check()
                )
            ]
            return {
                "enabled": self.enabled, "registered_servers": len(self._servers),
                "connected_servers": len(connected) - len(unhealthy),
                "available_tools": len(self.tool_registry.list()),
                "recent_failures": len(self.recent_failures), "unhealthy_servers": unhealthy,
            }

    def shutdown(self) -> bool:
        success = True
        for server_id in list(self._clients):
            success = self.disconnect(server_id) and success
        return success

    def _require_server(self, server_id: str) -> MCPServer:
        with self._lock:
            server = self._servers.get(server_id)
        if server is None:
            raise KeyError(f"Unknown MCP server: {server_id}")
        return server

    def _failure(self, server_id: str, tool_id: str | None, exc: Exception) -> None:
        self.recent_failures.append({"server_id": server_id, "tool_id": tool_id or "", "error": type(exc).__name__})
        LOGGER.warning("server=%s tool=%s error=%s", server_id, tool_id, type(exc).__name__)

    def _emit(self, event_type, payload: dict[str, Any], high: bool = False) -> None:
        if self.event_bus:
            self.event_bus.emit(event_type, "mcp", payload, priority=EventPriority.HIGH if high else EventPriority.NORMAL)
