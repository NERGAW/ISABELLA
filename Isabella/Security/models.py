"""Immutable policy decisions and one-time confirmation requests."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
import uuid


class PolicyDecision(str, Enum):
    ALLOW = "ALLOW"
    CONFIRM = "CONFIRM"
    DENY = "DENY"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ConfirmationRequest:
    skill_id: str
    arguments: dict[str, Any]
    risk_level: str
    created_at: datetime
    expires_at: datetime
    source_request_id: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    @property
    def expired(self) -> bool:
        return utc_now() >= self.expires_at


@dataclass(frozen=True)
class PolicyResult:
    decision: PolicyDecision
    reason: str
    confirmation: ConfirmationRequest | None = None

