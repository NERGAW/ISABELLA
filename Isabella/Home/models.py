"""Safe Home device and telemetry contracts."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class DeviceStatus(str, Enum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    ERROR = "ERROR"


class DeviceRisk(str, Enum):
    SAFE = "SAFE"
    CAUTION = "CAUTION"
    CRITICAL = "CRITICAL"


ALLOWED_CAPABILITIES = frozenset({"switch", "light", "temperature", "humidity", "motion", "battery", "relay"})


@dataclass
class HomeDevice:
    device_id: str
    name: str
    type: str
    status: DeviceStatus
    capabilities: tuple[str, ...]
    node_id: str
    risk_level: DeviceRisk
    last_seen: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    simulated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"device_id": self.device_id, "name": self.name, "type": self.type,
                "status": self.status.value, "capabilities": list(self.capabilities),
                "last_seen": self.last_seen, "node_id": self.node_id,
                "risk_level": self.risk_level.value, "metadata": dict(self.metadata),
                "simulated": self.simulated}


@dataclass(frozen=True)
class Telemetry:
    device_id: str
    capability: str
    value: int | float | bool | str
    unit: str | None
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return {"device_id": self.device_id, "capability": self.capability,
                "value": self.value, "unit": self.unit, "timestamp": self.timestamp}
