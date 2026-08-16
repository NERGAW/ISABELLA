"""Runtime facade and validated Home configuration."""

import json
from pathlib import Path
from typing import Any

from Isabella.Core.config import ConfigurationError, PROJECT_ROOT
from .devices import DeviceRegistry
from .gateway import HomeGateway
from .mqtt import MQTTAdapter

DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "home.json"


def load_home_config(path: Path | None = None) -> dict[str, Any]:
    target = path or DEFAULT_CONFIG_PATH
    try: value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc: raise ConfigurationError(f"Invalid Home configuration: {target}") from exc
    required = {"enabled", "gateway_node_id", "offline_after_seconds", "mqtt", "devices"}
    if not isinstance(value, dict) or required - value.keys() or not isinstance(value["devices"], list): raise ConfigurationError("Home configuration is missing required fields")
    mqtt = value["mqtt"]
    if not isinstance(mqtt, dict) or {"enabled", "host", "port", "authentication_required", "username_env", "password_env", "tls"} - mqtt.keys(): raise ConfigurationError("MQTT configuration is invalid")
    if not 5 <= float(value["offline_after_seconds"]) <= 3600 or not 1 <= int(mqtt["port"]) <= 65535: raise ConfigurationError("Home limits are invalid")
    return value


class HomeManager:
    def __init__(self, config, *, event_bus=None, context=None, controller=None) -> None:
        self.config = config; self.enabled = bool(config["enabled"]); self.registry = DeviceRegistry(config["devices"])
        self.gateway = HomeGateway(self.registry, event_bus=event_bus, context=context, controller=controller, offline_after_seconds=config["offline_after_seconds"])
        self.mqtt = MQTTAdapter(config["mqtt"], self.gateway.telemetry, self.gateway.heartbeat); self.gateway.bind_mqtt(self.mqtt)

    @classmethod
    def from_config(cls, path: Path | None = None, **kwargs): return cls(load_home_config(path), **kwargs)
    def start(self): return True if not self.enabled else self.gateway.start()
    def shutdown(self): return self.gateway.shutdown()
    def health_check(self): return {"enabled": self.enabled, **self.gateway.diagnostics()}
    def list_devices(self): return [item.to_dict() for item in self.registry.list()]
    def command(self, device_id, command): return self.gateway.command(device_id, command)
    def ingest_telemetry(self, device_id, payload): return self.gateway.telemetry(device_id, payload)
