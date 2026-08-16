from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class AgentTask:
    text: str
    context: dict[str, Any] = field(default_factory=dict)
    hop: int = 1


@dataclass(frozen=True)
class AgentResult:
    agent_id: str
    success: bool
    output: Any = None
    error: str | None = None


@dataclass(frozen=True)
class Agent:
    id: str
    description: str
    capabilities: tuple[str, ...]
    allowed_skills: tuple[str, ...]
    required_context: tuple[str, ...]

    def execute(self, task: AgentTask, handler: Callable[["Agent", AgentTask], Any]) -> AgentResult:
        """Execute only a coordinator-supplied, allowlisted handler."""
        try:
            return AgentResult(self.id, True, handler(self, task))
        except Exception as exc:
            return AgentResult(self.id, False, error=type(exc).__name__)
