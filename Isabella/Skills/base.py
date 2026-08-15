"""Core contracts shared by all authorized skills."""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RiskLevel(str, Enum):
    SAFE = "SAFE"
    CAUTION = "CAUTION"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class ParameterSpec:
    value_type: type | tuple[type, ...]
    required: bool = True


@dataclass(frozen=True)
class SkillResult:
    success: bool
    skill_id: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    status: str = "completed"

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "success": self.success,
            "skill_id": self.skill_id,
            "message": self.message,
            "data": self.data,
            "status": self.status,
        }
        if self.error_code:
            result["error_code"] = self.error_code
        return result


SkillExecutor = Callable[[dict[str, Any]], SkillResult]


@dataclass(frozen=True)
class SkillDefinition:
    id: str
    name: str
    description: str
    category: str
    parameters: dict[str, ParameterSpec]
    risk_level: RiskLevel
    executor: SkillExecutor
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.id or "." not in self.id:
            raise ValueError("skill id must be qualified")
        if not self.name or not self.category:
            raise ValueError("skill name and category are required")
