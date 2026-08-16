"""Read-mostly adapter between the Qt UI and existing subsystems."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, is_dataclass
import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from Isabella.Core.config import PROJECT_ROOT
from Isabella.Security import PolicyDecision
from Isabella.Skills.base import RiskLevel
from .models import ControlCenterSnapshot


_SECRET_WORDS = ("password", "senha", "token", "secret", "credential", "private_key")


def _public(value: Any) -> Any:
    if is_dataclass(value):
        value = asdict(value)
    elif hasattr(value, "to_dict"):
        value = value.to_dict()
    if isinstance(value, dict):
        return {str(k): _public(v) for k, v in value.items() if not any(x in str(k).casefold() for x in _SECRET_WORDS)}
    if isinstance(value, (list, tuple, set)):
        return [_public(item) for item in value]
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class ControlCenterController(QObject):
    snapshot_ready = Signal(object)
    error = Signal(str)
    action_completed = Signal(str, bool, str)

    def __init__(self, runtime, interval_ms: int = 1500) -> None:
        super().__init__()
        self.runtime = runtime
        self.brain = runtime.brain
        self.administrative = False
        self.recent_events: deque[dict[str, Any]] = deque(maxlen=200)
        self.timer = QTimer(self)
        self.timer.setInterval(max(1000, min(interval_ms, 5000)))
        self.timer.timeout.connect(self.refresh)
        if runtime.event_bus:
            runtime.event_bus.subscribe("*", self._on_event)

    def start(self) -> None:
        self.refresh()
        self.timer.start()

    def shutdown(self) -> None:
        self.timer.stop()
        if self.runtime.event_bus:
            self.runtime.event_bus.unsubscribe("*", self._on_event)

    def set_administrative(self, enabled: bool) -> None:
        self.administrative = bool(enabled)

    def _on_event(self, event) -> None:
        self.recent_events.append(_public({
            "type": event.type, "source": event.source, "timestamp": event.timestamp,
            "correlation_id": event.correlation_id, "payload": event.payload,
        }))

    @Slot()
    def refresh(self) -> ControlCenterSnapshot | None:
        try:
            report = self.runtime.report()
            services = {name.upper(): item["state"] for name, item in report["services"].items()}
            diag = self.brain.diagnostics.check(detailed=True) if getattr(self.brain, "diagnostics", None) else {}
            statuses = {name.upper(): item.get("status", "UNKNOWN") for name, item in diag.get("statuses", {}).items()}
            overview = {**services, **statuses, "RUNTIME": report["runtime"]}
            skills = [{
                "id": skill.id, "category": skill.category, "risk": skill.risk_level.value,
                "enabled": skill.enabled,
            } for skill in self.brain.registry.list()]
            security = {
                "policy_loaded": True, "pending_confirmations": self.brain.security.pending_count,
                "expired_confirmations": self.brain.security.expired_count,
                "trusted_nodes": _public(getattr(self.runtime.device_security, "diagnostics", lambda: {})()),
            }
            memory = [_public(item) for item in self.brain.memory.list_memories()[-50:]]
            automations = [_public(item) for item in self.brain.automations.list()] if getattr(self.brain, "automations", None) else []
            scheduler = [_public(item) for item in self.brain.scheduler.list()] if getattr(self.brain, "scheduler", None) else []
            nodes = [_public(item) for item in self.runtime.nodes.list()] if self.runtime.nodes else []
            home = _public(self.runtime.home.health_check()) if self.runtime.home else {"status": "OFFLINE"}
            llm = getattr(self.brain, "llm", None)
            intelligence = {
                "provider": llm.__class__.__name__ if llm else "unavailable",
                "model": getattr(llm, "model", getattr(llm, "model_name", "unavailable")),
                "last_request_latency_ms": list(getattr(self.brain, "latencies_ms", []))[-1:] or [0],
                "router": self.brain.router.__class__.__name__, "planner": self.brain.planner.__class__.__name__,
            }
            snapshot = ControlCenterSnapshot(
                overview=overview, metrics={**diag.get("metrics", {}), "startup_ms": report["startup_ms"]},
                intelligence=intelligence, skills=skills, security=security, memory=memory,
                events=list(self.recent_events), automations=automations, scheduler=scheduler,
                nodes=nodes, home=home,
                current_mode=self.brain.modes.get_current_mode().id if getattr(self.brain, "modes", None) else "NORMAL",
                available_modes=tuple(mode.id for mode in self.brain.modes.list_modes()) if getattr(self.brain, "modes", None) else (),
                agents=list(getattr(getattr(self.brain, "orchestrator", None), "diagnostics", lambda: {"recent_activity": []})().get("recent_activity", [])),
                knowledge=[_public(item) for item in getattr(getattr(self.brain, "knowledge", None), "search_relations", lambda *_: [])("")],
            )
            self.snapshot_ready.emit(snapshot)
            return snapshot
        except Exception as exc:
            self.error.emit(str(exc))
            return None

    def search_memory(self, query: str) -> list[dict[str, Any]]:
        return [_public(item) for item in self.brain.memory.search(query, limit=50)]

    def delete_memory(self, key: str, memory_type: str | None = None) -> int:
        self._require_admin()
        self._authorize("control_center.memory_delete", {"key": key, "type": memory_type})
        return self.brain.memory.forget(key, memory_type)

    def set_automation_enabled(self, automation_id: str, enabled: bool):
        self._require_admin()
        self._authorize("control_center.automation_state", {"id": automation_id, "enabled": enabled})
        manager = self.brain.automations
        return manager.enable(automation_id) if enabled else manager.disable(automation_id)

    def cancel_task(self, task_id: str):
        self._require_admin()
        self._authorize("control_center.scheduler_cancel", {"id": task_id})
        return self.brain.scheduler.cancel(task_id)

    def restart_service(self, name: str) -> bool:
        self._require_admin()
        if name.casefold() == "core":
            raise PermissionError("O Core não pode ser reiniciado pelo Control Center.")
        self._authorize("control_center.service_restart", {"service": name})
        return self.runtime.restart_service(name)

    def set_mode(self, mode_id: str):
        return self.brain.modes.set_mode(mode_id, source="control_center")

    def read_logs(self, module: str = "", level: str = "", search: str = "", max_lines: int = 300) -> list[str]:
        path = PROJECT_ROOT / "logs" / "isabella.log"
        if not path.exists():
            return []
        with path.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - 131072))
            raw = stream.read().decode("utf-8", errors="replace")
        filters = [item.casefold() for item in (module, level, search) if item.strip()]
        lines = [line for line in raw.splitlines() if all(term in line.casefold() for term in filters)]
        return lines[-max(1, min(max_lines, 1000)):]

    def _require_admin(self) -> None:
        if not self.administrative:
            raise PermissionError("Ative explicitamente o modo administrativo para esta ação.")

    def _authorize(self, action: str, arguments: dict[str, Any]) -> None:
        result = self.brain.security.evaluate(action, arguments, RiskLevel.CAUTION, "control-center")
        if result.decision is not PolicyDecision.ALLOW:
            raise PermissionError("A política de segurança não autorizou esta ação administrativa.")
