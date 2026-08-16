from __future__ import annotations

import json
from pathlib import Path
import threading
import unicodedata

from Isabella.Core.config import ConfigurationError, PROJECT_ROOT
from Isabella.Events import EventType
from .models import Mode, ModePolicy
from .policies import validate_safety


DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "modes.json"


def load_modes_config(path: Path | None = None) -> dict:
    target = path or DEFAULT_CONFIG_PATH
    try:
        config = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Invalid modes configuration: {target}") from exc
    if not isinstance(config, dict) or not isinstance(config.get("modes"), list) or "initial_mode" not in config:
        raise ConfigurationError("Modes configuration is missing required fields")
    return config


class ModeManager:
    def __init__(self, config: dict, *, event_bus=None, context=None) -> None:
        self.event_bus = event_bus
        self.context = context
        self._lock = threading.RLock()
        self._modes: dict[str, Mode] = {}
        for item in config["modes"]:
            mode = Mode(
                id=str(item["id"]).upper(), name=str(item["name"]), description=str(item["description"]),
                enabled_skills=tuple(item.get("enabled_skills", ())), disabled_skills=tuple(item.get("disabled_skills", ())),
                interface_profile=str(item["interface_profile"]), voice_profile=str(item["voice_profile"]),
                research_allowed=bool(item["research_allowed"]), network_policy=str(item["network_policy"]),
                diagnostics_level=str(item["diagnostics_level"]),
            )
            validate_safety(mode)
            if mode.id in self._modes:
                raise ConfigurationError(f"Duplicate mode: {mode.id}")
            self._modes[mode.id] = mode
        self._current = self.validate_mode(config["initial_mode"])
        self._update_context()

    @classmethod
    def from_config(cls, path: Path | None = None, **kwargs):
        return cls(load_modes_config(path), **kwargs)

    @staticmethod
    def normalize(value: str) -> str:
        text = unicodedata.normalize("NFKD", str(value).strip()).encode("ascii", "ignore").decode().upper()
        aliases = {"ENGENHARIA": "ENGINEERING", "PRIVACIDADE": "PRIVACY", "CASA": "HOME", "MOVEL": "MOBILE", "PADRAO": "NORMAL"}
        return aliases.get(text, text)

    def validate_mode(self, mode_id: str) -> str:
        normalized = self.normalize(mode_id)
        if normalized not in self._modes:
            raise ValueError(f"Modo operacional inválido: {mode_id}")
        return normalized

    def get_current_mode(self) -> Mode:
        with self._lock:
            return self._modes[self._current]

    def list_modes(self) -> list[Mode]:
        return list(self._modes.values())

    def set_mode(self, mode_id: str, *, source: str = "user") -> Mode:
        try:
            target = self.validate_mode(mode_id)
            previous = self.get_current_mode().id
            self._emit(EventType.MODE_CHANGING, {"from": previous, "to": target, "source": source})
            with self._lock:
                self._current = target
            self._update_context()
            mode = self.get_current_mode()
            self._emit(EventType.MODE_CHANGED, {"from": previous, "to": target, "source": source})
            return mode
        except Exception as exc:
            self._emit(EventType.MODE_FAILED, {"requested": str(mode_id), "error": type(exc).__name__})
            raise

    def apply_policy(self, *, input_source: str | None = None) -> ModePolicy:
        mode = self._modes["MOBILE"] if str(input_source).upper() == "MOBILE_NODE" else self.get_current_mode()
        return ModePolicy(mode.id, mode.enabled_skills, mode.disabled_skills, mode.interface_profile, mode.voice_profile, mode.research_allowed, mode.network_policy, mode.diagnostics_level)

    def _update_context(self):
        if self.context:
            self.context.set("current_mode", self._current)

    def _emit(self, event_type, payload):
        if self.event_bus:
            self.event_bus.emit(event_type, "modes", payload)
