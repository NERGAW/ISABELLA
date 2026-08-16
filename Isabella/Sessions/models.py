"""Volatile multi-device session contracts."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import uuid


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class IsabellaSession:
    user_context: dict[str, Any]
    active_node: str
    working_memory_reference: str
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at: str = field(default_factory=now_iso)
    last_activity: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {"session_id": self.session_id, "user_context": dict(self.user_context),
                "active_node": self.active_node, "started_at": self.started_at,
                "last_activity": self.last_activity,
                "working_memory_reference": self.working_memory_reference}
