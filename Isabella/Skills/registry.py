"""Allowlisted skill registry with strict argument validation."""

from __future__ import annotations

import logging
from collections import deque
from time import perf_counter
from typing import Any

from .base import RiskLevel, SkillDefinition, SkillResult
from Isabella.Events import EventType


LOGGER = logging.getLogger("SKILL")


class SkillRegistry:
    def __init__(self, event_bus=None) -> None:
        self._skills: dict[str, SkillDefinition] = {}
        self.validation_latencies_ms: deque[float] = deque(maxlen=200)
        self.execution_latencies_ms: deque[float] = deque(maxlen=200)
        self.event_bus = event_bus

    def register(self, definition: SkillDefinition) -> None:
        if definition.id in self._skills:
            raise ValueError(f"Skill already registered: {definition.id}")
        self._skills[definition.id] = definition

    def get(self, skill_id: str) -> SkillDefinition | None:
        return self._skills.get(skill_id)

    def exists(self, skill_id: str) -> bool:
        return skill_id in self._skills

    def list(self) -> list[SkillDefinition]:
        return list(self._skills.values())

    def list_by_category(self, category: str) -> list[SkillDefinition]:
        return [skill for skill in self._skills.values() if skill.category == category]

    def validate_arguments(self, skill_id: str, arguments: dict[str, Any]) -> SkillResult | None:
        started = perf_counter()
        try:
            definition = self.get(skill_id)
            if definition is None:
                return SkillResult(False, skill_id, "Skill não autorizada.", error_code="UNKNOWN_SKILL", status="rejected")
            if not definition.enabled:
                return SkillResult(False, skill_id, "Skill desabilitada.", error_code="SKILL_DISABLED", status="rejected")
            if not isinstance(arguments, dict):
                return SkillResult(False, skill_id, "Argumentos inválidos.", error_code="INVALID_ARGUMENTS", status="rejected")
            extras = sorted(arguments.keys() - definition.parameters.keys())
            if extras:
                return SkillResult(False, skill_id, f"Argumentos extras: {', '.join(extras)}.", error_code="EXTRA_ARGUMENTS", status="rejected")
            missing = [name for name, spec in definition.parameters.items() if spec.required and name not in arguments]
            if missing:
                return SkillResult(False, skill_id, f"Argumentos ausentes: {', '.join(missing)}.", error_code="MISSING_ARGUMENTS", status="rejected")
            for name, value in arguments.items():
                expected = definition.parameters[name].value_type
                if isinstance(value, bool) or not isinstance(value, expected):
                    return SkillResult(False, skill_id, f"Tipo inválido para {name}.", error_code="INVALID_ARGUMENT_TYPE", status="rejected")
            return None
        finally:
            latency = (perf_counter() - started) * 1000
            self.validation_latencies_ms.append(latency)

    def execute(self, skill_id: str, arguments: dict[str, Any], confirmed: bool = False) -> SkillResult:
        started = perf_counter()
        if self.event_bus:
            self.event_bus.emit(EventType.SKILL_STARTED, "skills", {"skill_id": skill_id, "status": "started"})
        validation_error = self.validate_arguments(skill_id, arguments)
        if validation_error:
            self._emit_result(validation_error, started, arguments)
            return validation_error
        definition = self._skills[skill_id]
        if definition.risk_level == RiskLevel.CRITICAL and not confirmed:
            result = SkillResult(
                False,
                skill_id,
                "Confirmação explícita necessária.",
                data={"arguments": arguments},
                error_code="CONFIRMATION_REQUIRED",
                status="confirmation_required",
            )
            self._emit_result(result, started, arguments)
            return result
        execution_started = perf_counter()
        try:
            result = definition.executor(arguments)
            LOGGER.info("skill=%s status=%s", skill_id, result.status)
            self._emit_result(result, started, arguments)
            return result
        except Exception:
            LOGGER.exception("skill=%s failed", skill_id)
            result = SkillResult(False, skill_id, "A execução da Skill falhou.", error_code="EXECUTION_ERROR", status="failed")
            self._emit_result(result, started, arguments)
            return result
        finally:
            latency = (perf_counter() - execution_started) * 1000
            self.execution_latencies_ms.append(latency)
            LOGGER.info("skill=%s execution_latency_ms=%.3f", skill_id, latency)

    def _emit_result(self, result: SkillResult, started: float, arguments: dict[str, Any]) -> None:
        if not self.event_bus:
            return
        event_type = EventType.SKILL_COMPLETED if result.success else EventType.SKILL_FAILED
        self.event_bus.emit(
            event_type, "skills",
            {
                "skill_id": result.skill_id, "status": result.status,
                "risk_level": self._skills[result.skill_id].risk_level.value if result.skill_id in self._skills else "UNKNOWN",
                "success": result.success, "message": result.message,
                "data": result.data,
                "arguments": {
                    key: value for key, value in arguments.items()
                    if not any(secret in key.lower() for secret in ("password", "senha", "token", "secret", "key"))
                },
                "duration_ms": (perf_counter() - started) * 1000,
            },
        )

    @property
    def average_validation_latency_ms(self) -> float:
        return sum(self.validation_latencies_ms) / len(self.validation_latencies_ms) if self.validation_latencies_ms else 0.0

    @property
    def average_execution_latency_ms(self) -> float:
        return sum(self.execution_latencies_ms) / len(self.execution_latencies_ms) if self.execution_latencies_ms else 0.0
