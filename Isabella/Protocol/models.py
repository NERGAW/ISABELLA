"""Transport-neutral ISABELLA Protocol v1 models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
import uuid

from .version import PROTOCOL_VERSION


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class MessageType(str, Enum):
    HELLO = "HELLO"
    WELCOME = "WELCOME"
    HEARTBEAT = "HEARTBEAT"
    STATUS = "STATUS"
    CAPABILITIES = "CAPABILITIES"
    COMMAND_REQUEST = "COMMAND_REQUEST"
    COMMAND_RESULT = "COMMAND_RESULT"
    CHAT_REQUEST = "CHAT_REQUEST"
    CHAT_RESULT = "CHAT_RESULT"
    NOTIFICATION = "NOTIFICATION"
    NOTIFICATION_ACK = "NOTIFICATION_ACK"
    NOTIFICATION_ACTION = "NOTIFICATION_ACTION"
    SESSION_HANDOFF = "SESSION_HANDOFF"
    EVENT = "EVENT"
    TELEMETRY = "TELEMETRY"
    ERROR = "ERROR"
    GOODBYE = "GOODBYE"


class NodeType(str, Enum):
    PRIMARY = "PRIMARY"
    COMPUTER = "COMPUTER"
    SMARTPHONE = "SMARTPHONE"
    HOME = "HOME"
    EMBEDDED = "EMBEDDED"
    WEARABLE = "WEARABLE"
    PANEL = "PANEL"


@dataclass(frozen=True)
class NodeIdentity:
    node_id: str
    node_type: NodeType
    name: str
    protocol_version: str = PROTOCOL_VERSION
    capabilities: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"node_id": self.node_id, "node_type": self.node_type.value, "name": self.name, "protocol_version": self.protocol_version, "capabilities": list(self.capabilities)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NodeIdentity":
        return cls(str(data["node_id"]), NodeType(data["node_type"]), str(data["name"]), str(data["protocol_version"]), tuple(data.get("capabilities", [])))


@dataclass(frozen=True)
class ProtocolMessage:
    type: MessageType
    source: str
    destination: str
    payload: dict[str, Any]
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    protocol_version: str = PROTOCOL_VERSION
    timestamp: str = field(default_factory=now_iso)
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "protocol_version": self.protocol_version, "type": self.type.value, "source": self.source, "destination": self.destination, "timestamp": self.timestamp, "correlation_id": self.correlation_id, "payload": self.payload}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProtocolMessage":
        return cls(MessageType(data["type"]), str(data["source"]), str(data["destination"]), dict(data["payload"]), str(data["id"]), str(data["protocol_version"]), str(data["timestamp"]), str(data["correlation_id"]))


@dataclass(frozen=True)
class ProtocolError:
    code: str
    message: str
    request_id: str | None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "request_id": self.request_id, "details": self.details}
