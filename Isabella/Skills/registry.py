"""Allowlisted skill registry with strict argument validation."""

from __future__ import annotations

import logging
from collections import deque
from time import perf_counter
from typing import Any

from .base import SkillDefinition, SkillResult
from Isabella.Events import EventType
from Isabella.Security import PolicyDecision, SecurityPolicyEngine


LOGGER = logging.getLogger("SKILL")
SECRET_KEYS = ("password", "senha", "token", "secret", "key", "credential", "credencial")


def _safe_event_value(value):
    if isinstance(value, dict):
        return {
            key: _safe_event_value(item) for key, item in value.items()
            if not any(secret in str(key).lower() for secret in SECRET_KEYS)
        }
    if isinstance(value, (list, tuple)):
        return [_safe_event_value(item) for item in value]
    if isinstance(value, str) and any(
        term in value.casefold() for term in
        ("password", "senha", "token", "api key", "private key", "secret", "segredo", "credencial")
    ):
        return "[REDACTED]"
    return value


class SkillRegistry:
    def __init__(self, event_bus=None, policy_engine=None) -> None:
        self._skills: dict[str, SkillDefinition] = {}
        self.validation_latencies_ms: deque[float] = deque(maxlen=200)
        self.execution_latencies_ms: deque[float] = deque(maxlen=200)
        self.event_bus = event_bus
        self.policy_engine = policy_engine or SecurityPolicyEngine.from_config(event_bus=event_bus)

    def register(self, definition: SkillDefinition) -> None:
        if definition.id in self._skills:
            raise ValueError(f"Skill already registered: {definition.id}")
        self._skills[definition.id] = definition

    def unregister(self, skill_id: str) -> bool:
        return self._skills.pop(skill_id, None) is not None

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
                bool_not_expected = isinstance(value, bool) and expected is not bool and not (
                    isinstance(expected, tuple) and bool in expected
                )
                if bool_not_expected or not isinstance(value, expected):
                    return SkillResult(False, skill_id, f"Tipo inválido para {name}.", error_code="INVALID_ARGUMENT_TYPE", status="rejected")
            return None
        finally:
            latency = (perf_counter() - started) * 1000
            self.validation_latencies_ms.append(latency)

    def execute(
        self, skill_id: str, arguments: dict[str, Any], confirmed: bool = False,
        *, source_request_id: str = "direct", confirmation_id: str | None = None,
        confirmation_source: str = "untrusted",
    ) -> SkillResult:
        started = perf_counter()
        if self.event_bus:
            self.event_bus.emit(EventType.SKILL_STARTED, "skills", {"skill_id": skill_id, "status": "started"})
        validation_error = self.validate_arguments(skill_id, arguments)
        if validation_error:
            self._emit_result(validation_error, started, arguments)
            return validation_error
        definition = self._skills[skill_id]
        if confirmation_id:
            policy = self.policy_engine.confirm(
                confirmation_id, skill_id, arguments, source=confirmation_source,
            )
        else:
            # `confirmed` is deliberately ignored: a boolean can be forged by an LLM or caller.
            policy = self.policy_engine.evaluate(
                skill_id, arguments, definition.risk_level, source_request_id,
            )
        if policy.decision is PolicyDecision.CONFIRM:
            result = SkillResult(
                False,
                skill_id,
                "Confirmação explícita necessária.",
                data={
                    "arguments": dict(arguments),
                    "confirmation_id": policy.confirmation.id,
                    "expires_at": policy.confirmation.expires_at.isoformat(),
                },
                error_code="CONFIRMATION_REQUIRED",
                status="confirmation_required",
            )
            self._emit_result(result, started, arguments)
            return result
        if policy.decision is PolicyDecision.DENY:
            error_code = "CONFIRMATION_EXPIRED" if policy.reason == "confirmation_expired" else "SECURITY_DENIED"
            result = SkillResult(
                False, skill_id, "A política de segurança negou a ação.",
                error_code=error_code, status="denied",
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
        event_data = (
            {"summary": result.message, "detailed": result.data.get("detailed", False)}
            if result.skill_id == "system.diagnostics" else result.data
        )
        self.event_bus.emit(
            event_type, "skills",
            {
                "skill_id": result.skill_id, "status": result.status,
                "risk_level": self._skills[result.skill_id].risk_level.value if result.skill_id in self._skills else "UNKNOWN",
                "success": result.success, "message": result.message,
                "data": _safe_event_value(event_data),
                "arguments": _safe_event_value(arguments),
                "duration_ms": (perf_counter() - started) * 1000,
            },
        )

    @property
    def average_validation_latency_ms(self) -> float:
        return sum(self.validation_latencies_ms) / len(self.validation_latencies_ms) if self.validation_latencies_ms else 0.0

    @property
    def average_execution_latency_ms(self) -> float:
        return sum(self.execution_latencies_ms) / len(self.execution_latencies_ms) if self.execution_latencies_ms else 0.0
