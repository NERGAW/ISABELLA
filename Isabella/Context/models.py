"""Typed, safe-default models for current operational context."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class ContextType(str, Enum):
    SESSION = "SESSION"
    APPLICATION = "APPLICATION"
    WINDOW = "WINDOW"
    PROJECT = "PROJECT"
    DEVICE = "DEVICE"
    VOICE = "VOICE"
    LAST_ACTION = "LAST_ACTION"
    LAST_RESULT = "LAST_RESULT"
    SYSTEM = "SYSTEM"


@dataclass(frozen=True)
class ActionContext:
    skill: str
    action: str
    arguments: dict[str, Any] = field(default_factory=dict)
    risk_level: str = "SAFE"
    timestamp: str = field(default_factory=now_iso)


@dataclass(frozen=True)
class ResultContext:
    success: bool
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    status: str = "completed"
    timestamp: str = field(default_factory=now_iso)


@dataclass(frozen=True)
class ResolvedReference:
    reference: str
    entity: str | None
    confidence: float
    source: str

    @property
    def resolved(self) -> bool:
        return self.entity is not None


@dataclass(frozen=True)
class ContextSnapshot:
    timestamp: str
    session_id: str
    active_application: str = "unavailable"
    active_window_title: str = "unavailable"
    current_project: str | None = None
    last_user_command: str | None = None
    last_assistant_response: str | None = None
    last_skill: str | None = None
    last_action: ActionContext | None = None
    last_result: ResultContext | None = None
    voice_state: str = "IDLE"
    system_state: dict[str, str] = field(default_factory=dict)
    connected_devices: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
