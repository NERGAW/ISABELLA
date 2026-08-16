"""Optional Runtime facade for local real-time Node transport."""

from __future__ import annotations

import ipaddress
import json
from pathlib import Path
from typing import Any

from Isabella.API.auth import RateLimiter, TokenAuthentication
from Isabella.Core.config import ConfigurationError, PROJECT_ROOT
from .websocket_server import WebSocketNodeServer


DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "transport.json"


def load_transport_config(path: Path | None = None) -> dict[str, Any]:
    target = path or DEFAULT_CONFIG_PATH
    try:
        config = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Invalid Transport configuration: {target}") from exc
    if not isinstance(config, dict) or "websocket" not in config:
        raise ConfigurationError("Transport configuration is missing websocket")
    ws = config["websocket"]
    required = {"enabled", "host", "port", "allow_remote", "heartbeat_seconds", "connection_timeout_seconds", "max_message_size", "token_file", "rate_limit", "rate_window_seconds", "event_allowlist"}
    if not isinstance(ws, dict) or required - ws.keys():
        raise ConfigurationError("WebSocket configuration is missing required fields")
    try:
        address = ipaddress.ip_address(ws["host"])
    except ValueError as exc:
        raise ConfigurationError("Transport host must be a literal IP") from exc
    if not ws["allow_remote"] and not address.is_loopback:
        raise ConfigurationError("Remote Transport binding requires allow_remote=true")
    if not 0 <= int(ws["port"]) <= 65535 or not 5 <= float(ws["heartbeat_seconds"]) <= 30:
        raise ConfigurationError("Transport port or heartbeat is invalid")
    if not 1 <= float(ws["connection_timeout_seconds"]) <= 120 or not 1024 <= int(ws["max_message_size"]) <= 1024 * 1024:
        raise ConfigurationError("Transport timeouts or size limits are invalid")
    if not 1 <= int(ws["rate_limit"]) <= 1000 or not isinstance(ws["event_allowlist"], list) or "*" in ws["event_allowlist"]:
        raise ConfigurationError("Transport rate/event allowlist is invalid")
    return config


class TransportManager:
    def __init__(self, config: dict[str, Any], *, node_manager, registry, event_bus=None, device_security=None) -> None:
        self.config = config
        ws = config["websocket"]
        self.enabled = bool(ws["enabled"])
        token_path = Path(ws["token_file"])
        authentication = TokenAuthentication(token_path if token_path.is_absolute() else PROJECT_ROOT / token_path, required=True)
        limiter = RateLimiter(int(ws["rate_limit"]), float(ws["rate_window_seconds"]))
        self.server = WebSocketNodeServer(ws, node_manager=node_manager, registry=registry, authentication=authentication, rate_limiter=limiter, event_bus=event_bus, device_security=device_security)
        self.event_bus = event_bus
        self._subscribed = False

    @classmethod
    def from_config(cls, *, node_manager, registry, event_bus=None, device_security=None, path: Path | None = None) -> "TransportManager":
        return cls(load_transport_config(path), node_manager=node_manager, registry=registry, event_bus=event_bus, device_security=device_security)

    def start(self) -> bool:
        if not self.enabled:
            return True
        started = self.server.start()
        if started and self.event_bus and self.server.authorized_events:
            for event_name in self.server.authorized_events:
                self.event_bus.subscribe(event_name, self.server.broadcast_event)
            self._subscribed = True
        return started

    def shutdown(self) -> bool:
        if self._subscribed and self.event_bus:
            for event_name in self.server.authorized_events:
                self.event_bus.unsubscribe(event_name, self.server.broadcast_event)
        self._subscribed = False
        return self.server.shutdown()

    def diagnostics(self) -> dict[str, Any]:
        details = self.server.diagnostics()
        details.update({"enabled": self.enabled, "event_allowlist": sorted(self.server.authorized_events), "host": self.server.host, "port": self.server.port})
        return details
