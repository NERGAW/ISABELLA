"""Declarative, serializable automation models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class TriggerType(str, Enum):
    EVENT = "EVENT"
    STATE_CHANGE = "STATE_CHANGE"
    MANUAL = "MANUAL"


class ConditionOperator(str, Enum):
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_than"
    CONTAINS = "contains"
    EXISTS = "exists"


@dataclass(frozen=True)
class AutomationTrigger:
    type: TriggerType
    event: str | None = None

    def __post_init__(self) -> None:
        if self.type is not TriggerType.MANUAL and not self.event:
            raise ValueError("Event and state-change triggers require an event name")


@dataclass(frozen=True)
class AutomationCondition:
    field: str
    operator: ConditionOperator
    value: Any = None


@dataclass(frozen=True)
class AutomationAction:
    skill: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class Automation:
    id: str
    name: str
    enabled: bool
    trigger: AutomationTrigger
    conditions: tuple[AutomationCondition, ...]
    actions: tuple[AutomationAction, ...]
    owner: str
    source: str
    cooldown_seconds: float = 5.0
    max_runs: int | None = None
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    last_run: str | None = None
    run_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "enabled": self.enabled,
            "trigger": {"type": self.trigger.type.value, "event": self.trigger.event},
            "conditions": [
                {"field": item.field, "operator": item.operator.value, "value": item.value}
                for item in self.conditions
            ],
            "actions": [{"skill": item.skill, "arguments": item.arguments} for item in self.actions],
            "owner": self.owner, "source": self.source,
            "cooldown_seconds": self.cooldown_seconds, "max_runs": self.max_runs,
            "created_at": self.created_at, "updated_at": self.updated_at,
            "last_run": self.last_run, "run_count": self.run_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Automation":
        trigger = data["trigger"]
        return cls(
            id=data["id"], name=data["name"], enabled=bool(data.get("enabled", False)),
            trigger=AutomationTrigger(TriggerType(trigger["type"]), trigger.get("event")),
            conditions=tuple(AutomationCondition(item["field"], ConditionOperator(item["operator"]), item.get("value")) for item in data.get("conditions", [])),
            actions=tuple(AutomationAction(item["skill"], dict(item.get("arguments", {}))) for item in data.get("actions", [])),
            owner=data.get("owner", "user"), source=data.get("source", "manual_structured"),
            cooldown_seconds=float(data.get("cooldown_seconds", 5)), max_runs=data.get("max_runs"),
            created_at=data.get("created_at", now_iso()), updated_at=data.get("updated_at", now_iso()),
            last_run=data.get("last_run"), run_count=int(data.get("run_count", 0)),
        )


@dataclass(frozen=True)
class AutomationRun:
    automation_id: str
    success: bool
    results: tuple[dict[str, Any], ...]
    error: str | None = None

