"""Timezone-aware scheduled task models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from Isabella.Skills.base import RiskLevel


class ScheduleType(str, Enum):
    ONE_TIME = "ONE_TIME"
    INTERVAL = "INTERVAL"
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    MISSED = "MISSED"


class MissedTaskPolicy(str, Enum):
    SKIP = "SKIP"
    RUN_ON_STARTUP = "RUN_ON_STARTUP"
    ASK = "ASK"


@dataclass
class ScheduledTask:
    id: str
    name: str
    enabled: bool
    schedule_type: ScheduleType
    skill: str
    arguments: dict[str, Any]
    risk_level: RiskLevel
    created_at: str
    next_run: str | None
    run_at: str | None = None
    recurrence: dict[str, Any] = field(default_factory=dict)
    last_run: str | None = None
    run_count: int = 0
    status: TaskStatus = TaskStatus.PENDING
    reminder_text: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "enabled": self.enabled,
            "schedule_type": self.schedule_type.value, "run_at": self.run_at,
            "recurrence": self.recurrence, "skill": self.skill,
            "arguments": self.arguments, "risk_level": self.risk_level.value,
            "created_at": self.created_at, "last_run": self.last_run,
            "next_run": self.next_run, "run_count": self.run_count,
            "status": self.status.value, "reminder_text": self.reminder_text,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScheduledTask":
        return cls(
            id=data["id"], name=data["name"], enabled=bool(data.get("enabled", True)),
            schedule_type=ScheduleType(data["schedule_type"]), skill=data["skill"],
            arguments=dict(data.get("arguments", {})), risk_level=RiskLevel(data["risk_level"]),
            created_at=data["created_at"], next_run=data.get("next_run"), run_at=data.get("run_at"),
            recurrence=dict(data.get("recurrence", {})), last_run=data.get("last_run"),
            run_count=int(data.get("run_count", 0)), status=TaskStatus(data.get("status", "PENDING")),
            reminder_text=data.get("reminder_text"),
        )


def parse_aware(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Scheduled timestamps must be timezone-aware")
    return result
