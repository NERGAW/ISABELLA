"""Thread-safe volatile state and conservative reference resolution."""

from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from collections import deque
from dataclasses import replace
from pathlib import Path
from time import monotonic, perf_counter
from typing import Any

from Isabella.Core.config import ConfigurationError, PROJECT_ROOT
from Isabella.Memory.models import MemoryType
from Isabella.Memory.retrieval import normalize
from .models import ActionContext, ContextSnapshot, ResolvedReference, ResultContext, now_iso
from .providers import WindowsContextProvider
from Isabella.Events import EventType


LOGGER = logging.getLogger("CONTEXT")
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "context.json"
SNAPSHOT_FIELDS = set(ContextSnapshot.__dataclass_fields__)


def load_context_config(path: Path | None = None) -> dict[str, Any]:
    target = path or DEFAULT_CONFIG_PATH
    try:
        config = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Invalid context configuration: {target}") from exc
    required = {"enabled", "active_window_lookup", "refresh_interval_seconds", "reference_confidence_threshold"}
    if not isinstance(config, dict) or required - config.keys():
        raise ConfigurationError("Context configuration is missing required fields")
    if not 0.5 <= float(config["refresh_interval_seconds"]) <= 30:
        raise ConfigurationError("Context refresh interval is invalid")
    if not 0.0 <= float(config["reference_confidence_threshold"]) <= 1.0:
        raise ConfigurationError("Context confidence threshold is invalid")
    return config


class ContextManager:
    def __init__(self, config: dict[str, Any], provider=None, memory=None, event_bus=None) -> None:
        self.config = config
        self.enabled = bool(config.get("enabled", True))
        self.provider = provider or WindowsContextProvider()
        self.memory = memory
        self.event_bus = event_bus
        self.confidence_threshold = float(config["reference_confidence_threshold"])
        self.refresh_interval = float(config["refresh_interval_seconds"])
        self._lock = threading.RLock()
        self._last_window_lookup = 0.0
        self.metrics = {
            "snapshot_ms": deque(maxlen=200),
            "reference_resolution_ms": deque(maxlen=200),
            "active_window_lookup_ms": deque(maxlen=200),
        }
        self.status = "ONLINE" if self.enabled else "OFFLINE"
        self._snapshot = self._new_snapshot()
        self._apply_project_memory()
        if self.event_bus:
            self.event_bus.subscribe(EventType.SKILL_COMPLETED.value, self._on_skill_event)
            self.event_bus.subscribe(EventType.SKILL_FAILED.value, self._on_skill_event)
            self.event_bus.subscribe(EventType.VOICE_COMMAND.value, self._on_voice_command)
            self.event_bus.subscribe(EventType.VISION_CAPTURE_COMPLETED.value, self._on_vision_capture)

    @classmethod
    def from_config(cls, memory=None, path: Path | None = None, event_bus=None) -> "ContextManager":
        return cls(load_context_config(path), memory=memory, event_bus=event_bus)

    @staticmethod
    def _new_snapshot() -> ContextSnapshot:
        return ContextSnapshot(timestamp=now_iso(), session_id=uuid.uuid4().hex)

    def _apply_project_memory(self) -> None:
        if not self.memory:
            return
        records = self.memory.recall("current_project_name", MemoryType.PROJECT)
        if records:
            self.set("current_project", records[0].value)

    def get_snapshot(self) -> ContextSnapshot:
        started = perf_counter()
        with self._lock:
            snapshot = replace(
                self._snapshot,
                timestamp=now_iso(),
                system_state=dict(self._snapshot.system_state),
                connected_devices=dict(self._snapshot.connected_devices),
                metadata=dict(self._snapshot.metadata),
            )
        self.metrics["snapshot_ms"].append((perf_counter() - started) * 1000)
        return snapshot

    def update(self, **values: Any) -> ContextSnapshot:
        unknown = values.keys() - SNAPSHOT_FIELDS
        if unknown:
            raise KeyError(f"Unknown context fields: {', '.join(sorted(unknown))}")
        values.pop("session_id", None)
        values["timestamp"] = now_iso()
        with self._lock:
            previous = self._snapshot
            self._snapshot = replace(previous, **values)
        if values.get("active_application") and values["active_application"] != previous.active_application:
            LOGGER.info("active_application=%s", values["active_application"])
        if self.event_bus:
            self.event_bus.emit(EventType.CONTEXT_UPDATED, "context", {"fields": sorted(values.keys() - {"timestamp"})})
        return self.get_snapshot()

    def _on_skill_event(self, event) -> None:
        payload = event.payload
        self.record_action(payload.get("skill_id", "unknown"), payload.get("arguments", {}), payload.get("risk_level", "UNKNOWN"))
        self.record_result(
            bool(payload.get("success")), str(payload.get("message", "")),
            payload.get("data", {}), str(payload.get("status", "unknown")),
        )

    def _on_voice_command(self, event) -> None:
        command = event.payload.get("command")
        if isinstance(command, str) and command:
            self.set("last_user_command", command)

    def _on_vision_capture(self, event) -> None:
        self.update(
            last_vision_source=event.payload.get("source"),
            last_capture_timestamp=event.payload.get("timestamp"),
            last_capture_window=event.payload.get("active_window"),
        )

    def get(self, name: str, default=None):
        if name not in SNAPSHOT_FIELDS:
            return default
        with self._lock:
            return getattr(self._snapshot, name)

    def set(self, name: str, value: Any) -> ContextSnapshot:
        if name not in SNAPSHOT_FIELDS or name in {"timestamp", "session_id"}:
            raise KeyError(f"Unknown or immutable context field: {name}")
        return self.update(**{name: value})

    def clear(self, name: str | None = None) -> ContextSnapshot:
        if name is None:
            return self.reset_session()
        defaults = self._new_snapshot()
        return self.set(name, getattr(defaults, name))

    def reset_session(self) -> ContextSnapshot:
        with self._lock:
            self._snapshot = self._new_snapshot()
        self._apply_project_memory()
        return self.get_snapshot()

    def record_action(self, skill: str, arguments: dict[str, Any], risk_level: str = "SAFE") -> None:
        action = ActionContext(skill, skill.rsplit(".", 1)[-1], dict(arguments), risk_level)
        self.update(last_skill=skill, last_action=action)
        LOGGER.info("last_action=%s", skill)

    def record_result(self, success: bool, message: str, data: dict[str, Any] | None = None, status: str = "completed") -> None:
        safe_data = {key: value for key, value in (data or {}).items() if key not in {"traceback", "exception"}}
        self.set("last_result", ResultContext(success, message, safe_data, status))

    def record_conversation(self, user_command: str, assistant_response: str) -> None:
        self.update(last_user_command=user_command, last_assistant_response=assistant_response)

    def set_system_state(self, name: str, state: str) -> None:
        current = dict(self.get("system_state", {}))
        current[name] = state
        self.set("system_state", current)

    def refresh_active_window(self, force: bool = False) -> ContextSnapshot:
        if not self.enabled or not self.config.get("active_window_lookup", True):
            return self.get_snapshot()
        now = monotonic()
        if not force and now - self._last_window_lookup < self.refresh_interval:
            return self.get_snapshot()
        started = perf_counter()
        try:
            window = self.provider.active_window()
            application = window.application if window.available else "unavailable"
            title = window.title if window.available else "unavailable"
            self.status = "ONLINE" if window.available else "DEGRADED"
            self.update(active_application=application, active_window_title=title)
        except Exception as exc:
            self.status = "DEGRADED"
            LOGGER.warning("active_window_unavailable error=%s", type(exc).__name__)
            self.update(active_application="unavailable", active_window_title="unavailable")
        finally:
            self._last_window_lookup = now
            self.metrics["active_window_lookup_ms"].append((perf_counter() - started) * 1000)
        return self.get_snapshot()

    def refresh_devices(self) -> dict[str, str]:
        try:
            devices = self.provider.connected_devices()
        except Exception:
            devices = {}
        self.set("connected_devices", devices)
        return devices

    def resolve_reference(self, text: str) -> ResolvedReference:
        started = perf_counter()
        normalized = normalize(text)
        reference = next((item for item in ("o programa que esta aberto", "esse programa", "o aplicativo", "o navegador", "ele", "ela", "isso") if item in normalized), "")
        entity = None
        confidence = 0.0
        source = "none"
        if reference:
            snapshot = self.get_snapshot()
            action_entity = None
            if snapshot.last_action and snapshot.last_action.skill.startswith("applications."):
                action_entity = snapshot.last_action.arguments.get("name")
            active = snapshot.active_application if snapshot.active_application != "unavailable" else None
            if active in {"python", "pythonw", "isabella"} or "i.s.a.b.e.l.l.a" in snapshot.active_window_title.lower():
                active = None
            if reference == "o programa que esta aberto" and active:
                entity, confidence, source = active, 0.95, "active_application"
            elif reference == "o navegador" and self.memory:
                preferred = self.memory.recall("preferred_browser", MemoryType.PREFERENCE)
                if preferred:
                    entity, confidence, source = preferred[0].value, 0.95, "memory_preference"
            elif action_entity and active and normalize(str(action_entity)) != normalize(active):
                confidence, source = 0.45, "ambiguous_action_and_window"
            elif action_entity:
                entity, confidence, source = str(action_entity), 0.95, "last_action"
            elif active:
                entity, confidence, source = active, 0.85, "active_application"
        if confidence < self.confidence_threshold:
            entity = None
        result = ResolvedReference(reference, entity, confidence, source)
        self.metrics["reference_resolution_ms"].append((perf_counter() - started) * 1000)
        if reference:
            LOGGER.info("reference resolved=%s confidence=%.2f", entity or "none", confidence)
        return result

    def relevant_context(self, text: str) -> str:
        normalized = normalize(text)
        snapshot = self.get_snapshot()
        lines: list[str] = []
        if any(word in normalized for word in ("ele", "ela", "isso", "programa", "aplicativo", "navegador")):
            lines.extend([
                f"Aplicativo ativo: {snapshot.active_application}",
                f"Última ação: {snapshot.last_action.skill if snapshot.last_action else 'nenhuma'}",
            ])
        if "projeto" in normalized and snapshot.current_project:
            lines.append(f"Projeto atual: {snapshot.current_project}")
        return "\n".join(lines)

    def shutdown(self) -> None:
        if not self.event_bus:
            return
        self.event_bus.unsubscribe(EventType.SKILL_COMPLETED.value, self._on_skill_event)
        self.event_bus.unsubscribe(EventType.SKILL_FAILED.value, self._on_skill_event)
        self.event_bus.unsubscribe(EventType.VOICE_COMMAND.value, self._on_voice_command)
        self.event_bus.unsubscribe(EventType.VISION_CAPTURE_COMPLETED.value, self._on_vision_capture)
