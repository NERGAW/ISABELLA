"""Validated CRUD facade and lifecycle for event automations."""

from __future__ import annotations

import json
import re
from pathlib import Path
import threading
from typing import Any

from Isabella.Core.config import ConfigurationError, PROJECT_ROOT
from Isabella.Events import EventType
from .engine import AutomationEngine
from .models import Automation, AutomationRun, now_iso
from .storage import AutomationStorage


DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "automations.json"
ID_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{2,79}$")


def load_automations_config(path: Path | None = None) -> dict[str, Any]:
    target = path or DEFAULT_CONFIG_PATH
    try:
        config = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Invalid automations configuration: {target}") from exc
    required = {"enabled", "database_path", "default_cooldown_seconds", "max_chain_depth", "max_actions", "max_action_retries"}
    if not isinstance(config, dict) or required - config.keys():
        raise ConfigurationError("Automations configuration is missing required fields")
    if not 0 <= float(config["default_cooldown_seconds"]) <= 86400:
        raise ConfigurationError("Automation cooldown is invalid")
    if not 1 <= int(config["max_chain_depth"]) <= 20 or not 1 <= int(config["max_actions"]) <= 20:
        raise ConfigurationError("Automation safety limits are invalid")
    if int(config["max_action_retries"]) != 0:
        raise ConfigurationError("Automatic action retries are disabled in this phase")
    return config


class AutomationManager:
    def __init__(self, config: dict[str, Any], *, registry, event_bus=None, storage: AutomationStorage | None = None) -> None:
        self.config = config
        self.enabled = bool(config["enabled"])
        self.registry = registry
        self.event_bus = event_bus
        path = Path(config["database_path"])
        self.storage = storage or AutomationStorage(path if path.is_absolute() else PROJECT_ROOT / path)
        self._lock = threading.RLock()
        self.engine = AutomationEngine(
            registry=registry, event_bus=event_bus, list_automations=self.list,
            save=self.storage.save, max_chain_depth=int(config["max_chain_depth"]),
        )

    @classmethod
    def from_config(cls, *, registry, event_bus=None, path: Path | None = None) -> "AutomationManager":
        return cls(load_automations_config(path), registry=registry, event_bus=event_bus)

    def start(self) -> bool:
        return True if not self.enabled else self.engine.start()

    def create_automation(self, specification: dict[str, Any]) -> Automation:
        automation = Automation.from_dict({
            **specification,
            "enabled": bool(specification.get("enabled", False)),
            "cooldown_seconds": specification.get("cooldown_seconds", self.config["default_cooldown_seconds"]),
        })
        self._validate(automation)
        if self.storage.get(automation.id):
            raise ValueError("Automation id already exists")
        self.storage.save(automation)
        self._emit(EventType.AUTOMATION_CREATED, automation)
        return automation

    def update_automation(self, automation_id: str, changes: dict[str, Any]) -> Automation:
        current = self._require(automation_id)
        protected = {"id", "created_at", "last_run", "run_count"}
        if protected & changes.keys():
            raise ValueError("Immutable automation fields cannot be updated")
        data = current.to_dict()
        data.update(changes)
        data["updated_at"] = now_iso()
        updated = Automation.from_dict(data)
        self._validate(updated)
        self.storage.save(updated)
        return updated

    def enable(self, automation_id: str) -> Automation:
        return self.update_automation(automation_id, {"enabled": True})

    def disable(self, automation_id: str) -> Automation:
        automation = self.update_automation(automation_id, {"enabled": False})
        self._emit(EventType.AUTOMATION_DISABLED, automation)
        return automation

    def delete(self, automation_id: str) -> bool:
        self._require(automation_id)
        return self.storage.delete(automation_id)

    def list(self) -> list[Automation]:
        return self.storage.list()

    def get(self, automation_id: str) -> Automation | None:
        return self.storage.get(automation_id)

    def run_manual(self, automation_id: str) -> AutomationRun:
        automation = self._require(automation_id)
        return self.engine.run(automation)

    def diagnostics(self) -> dict[str, Any]:
        items = self.list()
        persisted_last = max((item.last_run for item in items if item.last_run), default=None)
        return {
            "enabled": self.enabled, "storage_accessible": self.storage.health_check(),
            "automations_total": len(items), "automations_enabled": sum(item.enabled for item in items),
            "runs": sum(item.run_count for item in items), "failures": self.engine.failures,
            "last_execution": self.engine.last_execution or persisted_last,
        }

    def shutdown(self) -> bool:
        stopped = self.engine.shutdown()
        self.storage.close()
        return stopped

    def _validate(self, automation: Automation) -> None:
        if not ID_PATTERN.fullmatch(automation.id) or not automation.name:
            raise ValueError("Automation id or name is invalid")
        if not automation.actions or len(automation.actions) > int(self.config["max_actions"]):
            raise ValueError("Automation actions are invalid")
        if automation.cooldown_seconds < 0 or automation.max_runs is not None and automation.max_runs < 1:
            raise ValueError("Automation limits are invalid")
        for action in automation.actions:
            error = self.registry.validate_arguments(action.skill, action.arguments)
            if error:
                raise ValueError(f"Invalid automation action {action.skill}: {error.error_code}")

    def _require(self, automation_id: str) -> Automation:
        automation = self.storage.get(automation_id)
        if automation is None:
            raise KeyError(f"Unknown automation: {automation_id}")
        return automation

    def _emit(self, event_type, automation: Automation) -> None:
        if self.event_bus:
            self.event_bus.emit(event_type, "automations", {"automation_id": automation.id, "enabled": automation.enabled})
