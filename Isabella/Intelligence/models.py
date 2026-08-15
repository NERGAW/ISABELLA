"""Validated data models exchanged by the intelligence core."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Intent(str, Enum):
    CONVERSATION = "conversation"
    SINGLE_SKILL = "single_skill"
    MULTI_STEP = "multi_step"


def _validate_arguments(arguments: dict[str, Any]) -> None:
    if not isinstance(arguments, dict):
        raise TypeError("arguments must be a dictionary")
    for key in arguments:
        if not isinstance(key, str):
            raise ValueError("argument names must be strings")


@dataclass(frozen=True)
class SkillRequest:
    skill: str
    arguments: dict[str, Any]

    def __post_init__(self) -> None:
        if not self.skill or "." not in self.skill:
            raise ValueError("skill must use a qualified name")
        _validate_arguments(self.arguments)

    def to_dict(self) -> dict[str, Any]:
        return {"skill": self.skill, "arguments": self.arguments}


@dataclass(frozen=True)
class PlanStep:
    id: int
    skill: str
    arguments: dict[str, Any]
    depends_on: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.id < 1:
            raise ValueError("step id must be positive")
        if not self.skill or "." not in self.skill:
            raise ValueError("step skill must use a qualified name")
        _validate_arguments(self.arguments)
        if any(item < 1 or item >= self.id for item in self.depends_on):
            raise ValueError("dependencies must reference earlier steps")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "skill": self.skill,
            "arguments": self.arguments,
            "depends_on": self.depends_on,
        }


@dataclass(frozen=True)
class Plan:
    steps: list[PlanStep]
    error: str | None = None

    def __post_init__(self) -> None:
        if self.error and self.steps:
            raise ValueError("an invalid plan cannot contain steps")
        ids = [step.id for step in self.steps]
        if ids != list(range(1, len(ids) + 1)):
            raise ValueError("plan step ids must be sequential")

    def to_dict(self) -> dict[str, Any]:
        if self.error:
            return {"steps": [], "error": self.error}
        return {"steps": [step.to_dict() for step in self.steps]}


@dataclass(frozen=True)
class BrainResponse:
    response_type: Intent
    message: str
    skill_request: SkillRequest | None = None
    plan: Plan | None = None
    skill_results: tuple[Any, ...] = ()

    def __post_init__(self) -> None:
        if self.response_type == Intent.SINGLE_SKILL and not self.skill_request:
            raise ValueError("single_skill responses require a skill request")
        if self.response_type == Intent.MULTI_STEP and not self.plan:
            raise ValueError("multi_step responses require a plan")
