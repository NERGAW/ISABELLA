"""Data models shared by the memory layers."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class MemoryType(str, Enum):
    WORKING_MEMORY = "WORKING_MEMORY"
    PREFERENCE = "PREFERENCE"
    FACT = "FACT"
    PROJECT = "PROJECT"
    EPISODIC = "EPISODIC"


@dataclass(frozen=True)
class MemoryRecord:
    id: int
    type: MemoryType
    key: str
    value: str
    source: str
    created_at: str
    updated_at: str
    confidence: float = 1.0
    tags: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    active: bool = True


@dataclass(frozen=True)
class WorkingMessage:
    role: str
    text: str
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
