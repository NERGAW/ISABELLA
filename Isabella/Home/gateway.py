"""Security boundary between Core and allowlisted Home devices."""

from collections import deque
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from Isabella.Events import EventType
from .devices import DeviceRegistry, VirtualDeviceAdapter
from .models import DeviceRisk, DeviceStatus, HomeDevice, Telemetry, now_iso


class HomeGateway:
    def __init__(self, registry: DeviceRegistry, *, event_bus=None, context=None, controller=None, offline_after_seconds=60) -> None:
        self.registry = registry; self.event_bus = event_bus; self.context = context; self.controller = controller
        self.offline_after_seconds = float(offline_after_seconds)
        self.virtual = VirtualDeviceAdapter(registry)
        self.mqtt = None
        self.latest_telemetry: dict[tuple[str, str], Telemetry] = {}
        self.telemetry_errors = 0; self.command_failures = 0
        self.command_latencies_ms = deque(maxlen=200)

    def bind_mqtt(self, mqtt) -> None: self.mqtt = mqtt

    def start(self) -> bool:
        self.virtual.start()
        for device in self.registry.list():
            if device.status is DeviceStatus.ONLINE: self._emit(EventType.HOME_DEVICE_ONLINE, device)
        self._sync(); return bool(not self.mqtt or self.mqtt.start())

    def heartbeat(self, device_id: str, payload: dict[str, Any] | None = None) -> HomeDevice:
        device = self.registry.require(device_id); previous = device.status
        if payload and set(payload) - {"status", "timestamp"}: raise ValueError("Heartbeat payload is invalid")
        device.status = DeviceStatus.ONLINE; device.last_seen = now_iso()
        if previous is not DeviceStatus.ONLINE: self._emit(EventType.HOME_DEVICE_ONLINE, device)
        self._sync(); return device

    def telemetry(self, device_id: str, payload: dict[str, Any]) -> Telemetry:
        try:
            if set(payload) != {"capability", "value", "unit", "timestamp"}: raise ValueError("Telemetry fields are invalid")
            device = self.registry.require(device_id); capability = payload["capability"]
            if capability not in device.capabilities or capability not in {"temperature", "humidity", "motion", "battery", "switch", "light"}: raise ValueError("Telemetry capability is not allowlisted")
            if isinstance(payload["value"], (dict, list)) or not isinstance(payload["value"], (int, float, bool, str)): raise ValueError("Telemetry value is invalid")
            stamp = datetime.fromisoformat(str(payload["timestamp"]));
            if stamp.tzinfo is None: raise ValueError("Telemetry timestamp must be timezone-aware")
            item = Telemetry(device_id, capability, payload["value"], str(payload["unit"]) if payload["unit"] is not None else None, stamp.isoformat())
            self.latest_telemetry[(device_id, capability)] = item; self.heartbeat(device_id)
            if self.event_bus: self.event_bus.emit(EventType.HOME_TELEMETRY, "home", item.to_dict())
            self._sync(); return item
        except Exception:
            self.telemetry_errors += 1; raise

    def command(self, device_id: str, command: str) -> dict[str, Any]:
        started = perf_counter(); device = self.registry.require(device_id)
        allowed = {"light_on": "light", "light_off": "light", "get_temperature": "temperature", "get_status": None}
        capability = allowed.get(command)
        if command not in allowed or capability and capability not in device.capabilities: raise PermissionError("Command/capability is not allowlisted")
        if device.risk_level is DeviceRisk.CRITICAL and command not in {"get_temperature", "get_status"}:
            raise PermissionError("Critical Home actuators are disabled in this version")
        if device.status is not DeviceStatus.ONLINE: raise ConnectionError("Device is offline")
        if self.event_bus: self.event_bus.emit(EventType.HOME_COMMAND_STARTED, "home", {"device_id": device_id, "command": command, "risk_level": device.risk_level.value})
        try:
            if device.simulated: result = self.virtual.command(device, command)
            elif not self.mqtt or not self.mqtt.publish_command(device_id, command): raise ConnectionError("MQTT command delivery failed")
            else: result = {"accepted": True}
            if self.event_bus: self.event_bus.emit(EventType.HOME_COMMAND_COMPLETED, "home", {"device_id": device_id, "command": command})
            return result
        except Exception:
            self.command_failures += 1
            if self.event_bus: self.event_bus.emit(EventType.HOME_COMMAND_FAILED, "home", {"device_id": device_id, "command": command})
            raise
        finally: self.command_latencies_ms.append((perf_counter() - started) * 1000)

    def mark_offline(self, now: datetime | None = None) -> list[str]:
        current = now or datetime.now(timezone.utc); changed = []
        for device in self.registry.list():
            if device.simulated or device.status is not DeviceStatus.ONLINE or not device.last_seen: continue
            if (current - datetime.fromisoformat(device.last_seen)).total_seconds() >= self.offline_after_seconds:
                device.status = DeviceStatus.OFFLINE; changed.append(device.device_id); self._emit(EventType.HOME_DEVICE_OFFLINE, device)
        if changed: self._sync()
        return changed

    def diagnostics(self) -> dict[str, Any]:
        devices = self.registry.list(); mqtt_enabled = bool(self.mqtt and self.mqtt.config.get("enabled"))
        return {"gateway": "ONLINE", "broker": "ONLINE" if self.mqtt and self.mqtt.connected else "DISABLED" if not mqtt_enabled else "OFFLINE",
                "devices_online": sum(x.status is DeviceStatus.ONLINE for x in devices), "devices_offline": sum(x.status is DeviceStatus.OFFLINE for x in devices),
                "telemetry_errors": self.telemetry_errors + (self.mqtt.errors if self.mqtt else 0), "command_failures": self.command_failures}

    def shutdown(self) -> bool: return self.mqtt.shutdown() if self.mqtt else True

    def _sync(self):
        online = [item.device_id for item in self.registry.list() if item.status is DeviceStatus.ONLINE]
        if self.context:
            metadata = dict(self.context.get("metadata", {})); metadata["home_devices_online"] = online
            metadata["home_latest_states"] = {f"{d}:{c}": t.value for (d, c), t in self.latest_telemetry.items()}; self.context.set("metadata", metadata)
        if self.controller: self.controller.update_subsystem("HOME", f"ONLINE | Devices: {len(online)}")

    def _emit(self, kind, device):
        if self.event_bus: self.event_bus.emit(kind, "home", {"device_id": device.device_id, "status": device.status.value})
