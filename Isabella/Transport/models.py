"""Real-time transport connection models and bounded metrics."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
import uuid


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class ConnectionStatus(str, Enum):
    CONNECTING = "CONNECTING"
    ESTABLISHED = "ESTABLISHED"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"
    CLOSED = "CLOSED"
    ERROR = "ERROR"


@dataclass
class NodeConnection:
    node_id: str | None = None
    remote_address: str = "unknown"
    protocol_version: str = "1.0"
    status: ConnectionStatus = ConnectionStatus.CONNECTING
    connection_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    connected_at: str = field(default_factory=now_iso)
    last_seen: str = field(default_factory=now_iso)
    authenticated: bool = False
    pairing_id: str | None = None
    messages_received: int = 0
    messages_sent: int = 0
    errors: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"connection_id": self.connection_id, "node_id": self.node_id, "connected_at": self.connected_at, "last_seen": self.last_seen, "remote_address": self.remote_address, "protocol_version": self.protocol_version, "status": self.status.value, "authenticated": self.authenticated, "pairing_id": self.pairing_id, "messages_received": self.messages_received, "messages_sent": self.messages_sent, "errors": self.errors}
