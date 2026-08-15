"""Validated MCP server, tool and result models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from Isabella.Skills.base import RiskLevel


class MCPTransport(str, Enum):
    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable_http"


class MCPServerState(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    ERROR = "ERROR"


@dataclass(frozen=True)
class MCPServer:
    id: str
    name: str
    transport: MCPTransport
    command_or_url: str
    enabled: bool = True
    trusted: bool = False
    timeout: float = 30.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id or not self.id.replace("_", "").replace("-", "").isalnum():
            raise ValueError("MCP server id is invalid")
        if not self.name or not self.command_or_url:
            raise ValueError("MCP server name and command/url are required")
        if not 0.1 <= float(self.timeout) <= 300:
            raise ValueError("MCP server timeout is invalid")
        if self.transport is MCPTransport.STREAMABLE_HTTP and not self.command_or_url.startswith(("http://", "https://")):
            raise ValueError("Streamable HTTP server requires an HTTP(S) URL")


@dataclass(frozen=True)
class MCPTool:
    server_id: str
    name: str
    description: str
    input_schema: dict[str, Any]
    risk_level: RiskLevel = RiskLevel.CAUTION
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def skill_id(self) -> str:
        safe_name = "".join(character if character.isalnum() or character == "_" else "_" for character in self.name)
        safe_server = self.server_id.replace("-", "_")
        return f"mcp.{safe_server}.{safe_name}"


@dataclass(frozen=True)
class MCPToolResult:
    success: bool
    content: Any = None
    error: str | None = None

