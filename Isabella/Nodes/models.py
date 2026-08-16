"""Validated local models for known ISABELLA Nodes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from Isabella.Protocol import NodeIdentity, NodeType as ProtocolNodeType, PROTOCOL_VERSION, ProtocolMessage, MessageType


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class NodeType(str, Enum):
    PRIMARY_PC = "PRIMARY_PC"
    SECONDARY_PC = "SECONDARY_PC"
    MOBILE = "MOBILE"
    HOME = "HOME"
    HELMET = "HELMET"
    EMBEDDED = "EMBEDDED"
    DISPLAY = "DISPLAY"
    SENSOR = "SENSOR"


class NodeStatus(str, Enum):
    CONNECTING = "CONNECTING"
    ONLINE = "ONLINE"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"
    DISCONNECTED = "DISCONNECTED"
    ERROR = "ERROR"


class TrustState(str, Enum):
    UNTRUSTED = "UNTRUSTED"
    PAIRING = "PAIRING"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    PENDING = "PENDING_APPROVAL"
    TRUSTED = "TRUSTED"
    REVOKED = "REVOKED"


PROTOCOL_TYPE_MAP = {
    NodeType.PRIMARY_PC: ProtocolNodeType.PRIMARY,
    NodeType.SECONDARY_PC: ProtocolNodeType.COMPUTER,
    NodeType.MOBILE: ProtocolNodeType.SMARTPHONE,
    NodeType.HOME: ProtocolNodeType.HOME,
    NodeType.HELMET: ProtocolNodeType.WEARABLE,
    NodeType.EMBEDDED: ProtocolNodeType.EMBEDDED,
    NodeType.DISPLAY: ProtocolNodeType.PANEL,
    NodeType.SENSOR: ProtocolNodeType.EMBEDDED,
}


@dataclass
class Node:
    node_id: str
    name: str
    node_type: NodeType
    status: NodeStatus
    protocol_version: str = PROTOCOL_VERSION
    capabilities: tuple[str, ...] = ()
    last_seen: str | None = None
    connected_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    trust: TrustState = TrustState.UNTRUSTED

    def to_dict(self) -> dict[str, Any]:
        return {"node_id": self.node_id, "name": self.name, "node_type": self.node_type.value, "status": self.status.value, "protocol_version": self.protocol_version, "capabilities": list(self.capabilities), "last_seen": self.last_seen, "connected_at": self.connected_at, "metadata": self.metadata, "trust": self.trust.value}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Node":
        return cls(str(data["node_id"]), str(data["name"]), NodeType(data["node_type"]), NodeStatus(data["status"]), str(data.get("protocol_version", PROTOCOL_VERSION)), tuple(data.get("capabilities", [])), data.get("last_seen"), data.get("connected_at"), dict(data.get("metadata", {})), TrustState(data.get("trust", "UNTRUSTED")))

    def protocol_identity(self) -> NodeIdentity:
        return NodeIdentity(self.node_id, PROTOCOL_TYPE_MAP[self.node_type], self.name, self.protocol_version, self.capabilities)

    def hello(self, destination: str = "primary") -> ProtocolMessage:
        return ProtocolMessage(MessageType.HELLO, self.node_id, destination, {"identity": self.protocol_identity().to_dict()})
