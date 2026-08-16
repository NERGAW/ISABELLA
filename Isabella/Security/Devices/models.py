"""Immutable contracts for cryptographic device trust and pairing."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PairingState(str, Enum):
    UNTRUSTED = "UNTRUSTED"
    PAIRING = "PAIRING"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    TRUSTED = "TRUSTED"
    REVOKED = "REVOKED"


@dataclass
class DeviceRecord:
    node_id: str
    public_identity: str
    created_at: str
    trust_status: PairingState = PairingState.UNTRUSTED
    permissions: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"node_id": self.node_id, "public_identity": self.public_identity,
                "created_at": self.created_at, "trust_status": self.trust_status.value,
                "permissions": list(self.permissions), "metadata": self.metadata}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "DeviceRecord":
        return cls(str(value["node_id"]), str(value["public_identity"]),
                   str(value["created_at"]), PairingState(value["trust_status"]),
                   tuple(value.get("permissions", ())), dict(value.get("metadata", {})))


@dataclass
class PairingRequest:
    pairing_id: str
    node_id: str
    public_identity: str
    code_digest: str
    display_code: str
    created_at: datetime
    expires_at: datetime
    state: PairingState = PairingState.PAIRING
    used: bool = False
    requested_permissions: tuple[str, ...] = ()

    @property
    def expired(self) -> bool:
        return utc_now() >= self.expires_at
