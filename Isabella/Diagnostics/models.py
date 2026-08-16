"""Bounded, serializable diagnostics models."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class HealthStatus(str, Enum):
    ONLINE = "ONLINE"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"
    ERROR = "ERROR"
    UNKNOWN = "UNKNOWN"


class Subsystem(str, Enum):
    CORE = "CORE"
    LLM = "LLM"
    ROUTER = "ROUTER"
    PLANNER = "PLANNER"
    SKILLS = "SKILLS"
    VOICE_INPUT = "VOICE INPUT"
    VOICE_OUTPUT = "VOICE OUTPUT"
    HUD = "HUD"
    MEMORY = "MEMORY"
    CONTEXT = "CONTEXT"
    VISION = "VISION"
    EVENT_BUS = "EVENT BUS"
    SECURITY = "SECURITY"
    MCP = "MCP"
    RESEARCH = "RESEARCH"
    SKILL_FORGE = "SKILL FORGE"
    AUTOMATIONS = "AUTOMATIONS"
    SCHEDULER = "SCHEDULER"
    API = "API"
    NODES = "NODES"
    TRANSPORT = "TRANSPORT"


@dataclass(frozen=True)
class SubsystemHealth:
    subsystem: Subsystem
    status: HealthStatus
    details: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0
    checked_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "subsystem": self.subsystem.value, "status": self.status.value,
            "details": self.details, "latency_ms": self.latency_ms,
            "checked_at": self.checked_at,
        }


@dataclass(frozen=True)
class SystemMetrics:
    cpu_percent: float
    system_ram_percent: float
    process_memory_mb: float
    thread_count: int
    queue_sizes: dict[str, int]
    uptime_seconds: float


@dataclass(frozen=True)
class FailureRecord:
    subsystem: Subsystem
    status: HealthStatus
    observed_at: str
    reason: str


@dataclass(frozen=True)
class DiagnosticsReport:
    statuses: dict[str, SubsystemHealth]
    metrics: SystemMetrics
    summary: str
    detailed: bool
    generated_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "statuses": {name: health.to_dict() for name, health in self.statuses.items()},
            "metrics": asdict(self.metrics), "summary": self.summary,
            "detailed": self.detailed, "generated_at": self.generated_at,
        }
