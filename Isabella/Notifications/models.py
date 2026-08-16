"""Structured, transport-neutral notifications."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
import uuid


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class NotificationType(str, Enum):
    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    ERROR = "ERROR"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    REMINDER = "REMINDER"


@dataclass(frozen=True)
class Notification:
    type: NotificationType
    title: str
    message: str
    source: str
    priority: int = 1
    actions: tuple[str, ...] = ()
    expires_at: str | None = None
    target_node: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: str = field(default_factory=now_iso)

    @property
    def expired(self) -> bool:
        return bool(self.expires_at and datetime.fromisoformat(self.expires_at) <= datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "type": self.type.value, "title": self.title, "message": self.message,
                "source": self.source, "timestamp": self.timestamp, "priority": self.priority,
                "actions": list(self.actions), "expires_at": self.expires_at}
