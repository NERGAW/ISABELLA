"""Central, deterministic authorization for skill execution."""

from __future__ import annotations

from datetime import timedelta
from dataclasses import replace
from copy import deepcopy
import json
import logging
from pathlib import Path
import threading
from typing import Any

from Isabella.Core.config import ConfigurationError, PROJECT_ROOT
from Isabella.Events import EventType
from .models import ConfirmationRequest, PolicyDecision, PolicyResult, utc_now


LOGGER = logging.getLogger("SECURITY")
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "security.json"
TRUSTED_CONFIRMATION_SOURCES = frozenset({"hud", "voice", "cli", "user_input"})
RISK_LEVELS = frozenset({"SAFE", "CAUTION", "CRITICAL"})


def load_security_config(path: Path | None = None) -> dict[str, Any]:
    target = path or DEFAULT_CONFIG_PATH
    try:
        config = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Invalid security configuration: {target}") from exc
    required = {"confirmation_timeout_seconds", "risk_policies", "critical_confirmation_required", "logging_level"}
    if not isinstance(config, dict) or required - config.keys():
        raise ConfigurationError("Security configuration is missing required fields")
    timeout = float(config["confirmation_timeout_seconds"])
    if not 1 <= timeout <= 300:
        raise ConfigurationError("Security confirmation timeout is invalid")
    policies = config["risk_policies"]
    if not isinstance(policies, dict) or set(policies) != RISK_LEVELS:
        raise ConfigurationError("Security risk policies are invalid")
    try:
        for value in policies.values():
            PolicyDecision(value)
    except ValueError as exc:
        raise ConfigurationError("Security policy decision is invalid") from exc
    if policies["CRITICAL"] == PolicyDecision.ALLOW.value:
        raise ConfigurationError("CRITICAL actions cannot be configured as ALLOW")
    if bool(config["critical_confirmation_required"]) and policies["CRITICAL"] != PolicyDecision.CONFIRM.value:
        raise ConfigurationError("CRITICAL confirmation policy cannot be weakened")
    return config


class SecurityPolicyEngine:
    def __init__(self, config: dict[str, Any], event_bus=None) -> None:
        self.config = config
        self.event_bus = event_bus
        self.timeout = float(config["confirmation_timeout_seconds"])
        self._risk_policies = {
            name: PolicyDecision(decision) for name, decision in config["risk_policies"].items()
        }
        self._pending: dict[str, ConfirmationRequest] = {}
        self._lock = threading.RLock()

    @classmethod
    def from_config(cls, path: Path | None = None, event_bus=None) -> "SecurityPolicyEngine":
        return cls(load_security_config(path), event_bus=event_bus)

    def evaluate(
        self, skill_id: str, arguments: dict[str, Any], risk_level,
        source_request_id: str,
    ) -> PolicyResult:
        self.expire_pending()
        risk_name = getattr(risk_level, "value", str(risk_level))
        decision = self._risk_policies.get(risk_name, PolicyDecision.DENY)
        if risk_name == "CRITICAL" and decision is PolicyDecision.ALLOW:
            decision = PolicyDecision.CONFIRM
        if decision is PolicyDecision.ALLOW:
            self._emit(EventType.SECURITY_ALLOWED, skill_id, source_request_id)
            return PolicyResult(decision, "risk_policy_allows")
        if decision is PolicyDecision.DENY:
            self._emit(EventType.SECURITY_DENIED, skill_id, source_request_id)
            return PolicyResult(decision, "risk_policy_denies")
        now = utc_now()
        confirmation = ConfirmationRequest(
            skill_id=skill_id,
            arguments=deepcopy(arguments),
            risk_level=risk_name,
            created_at=now,
            expires_at=now + timedelta(seconds=self.timeout),
            source_request_id=source_request_id,
        )
        with self._lock:
            self._pending[confirmation.id] = confirmation
        self._emit(
            EventType.SECURITY_CONFIRMATION_REQUIRED, skill_id, source_request_id,
            {"confirmation_id": confirmation.id, "expires_at": confirmation.expires_at.isoformat()},
        )
        public_confirmation = replace(confirmation, arguments=deepcopy(confirmation.arguments))
        return PolicyResult(decision, "explicit_user_confirmation_required", public_confirmation)

    def confirm(
        self, confirmation_id: str, skill_id: str, arguments: dict[str, Any],
        *, source: str,
    ) -> PolicyResult:
        if source not in TRUSTED_CONFIRMATION_SOURCES:
            self._emit(EventType.SECURITY_DENIED, skill_id, None, {"reason": "untrusted_confirmation_source"})
            return PolicyResult(PolicyDecision.DENY, "untrusted_confirmation_source")
        with self._lock:
            request = self._pending.get(confirmation_id)
            if request is None:
                self._emit(EventType.SECURITY_DENIED, skill_id, None, {"reason": "unknown_or_used_confirmation"})
                return PolicyResult(PolicyDecision.DENY, "unknown_or_used_confirmation")
            if request.expired:
                self._pending.pop(confirmation_id, None)
                self._emit(EventType.SECURITY_EXPIRED, request.skill_id, request.source_request_id)
                return PolicyResult(PolicyDecision.DENY, "confirmation_expired")
            if request.skill_id != skill_id or request.arguments != arguments:
                self._emit(EventType.SECURITY_DENIED, skill_id, request.source_request_id, {"reason": "confirmation_mismatch"})
                return PolicyResult(PolicyDecision.DENY, "confirmation_mismatch")
            self._pending.pop(confirmation_id)
        self._emit(EventType.SECURITY_CONFIRMED, skill_id, request.source_request_id)
        return PolicyResult(
            PolicyDecision.ALLOW, "one_time_confirmation_consumed",
            replace(request, arguments=deepcopy(request.arguments)),
        )

    def cancel(self, confirmation_id: str) -> bool:
        with self._lock:
            request = self._pending.pop(confirmation_id, None)
        if request:
            self._emit(EventType.SECURITY_DENIED, request.skill_id, request.source_request_id, {"reason": "user_cancelled"})
        return request is not None

    def get_pending(self, confirmation_id: str) -> ConfirmationRequest | None:
        self.expire_pending()
        with self._lock:
            request = self._pending.get(confirmation_id)
            return replace(request, arguments=deepcopy(request.arguments)) if request else None

    def expire_pending(self) -> int:
        with self._lock:
            expired = [item for item in self._pending.values() if item.expired]
            for request in expired:
                self._pending.pop(request.id, None)
        for request in expired:
            self._emit(EventType.SECURITY_EXPIRED, request.skill_id, request.source_request_id)
        return len(expired)

    def _emit(self, event_type, skill_id: str, correlation_id: str | None, extra=None) -> None:
        LOGGER.log(
            getattr(logging, str(self.config.get("logging_level", "INFO")).upper(), logging.INFO),
            "decision=%s skill_id=%s", event_type.value, skill_id,
        )
        if self.event_bus:
            payload = {"skill_id": skill_id}
            payload.update(extra or {})
            self.event_bus.emit(event_type, "security", payload, correlation_id=correlation_id)
