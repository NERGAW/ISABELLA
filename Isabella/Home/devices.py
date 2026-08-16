"""Allowlisted devices and safe virtual laboratory implementations."""

import re
import threading
from typing import Any

from .models import ALLOWED_CAPABILITIES, DeviceRisk, DeviceStatus, HomeDevice, now_iso

DEVICE_ID = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


class DeviceRegistry:
    def __init__(self, configured: list[dict[str, Any]]) -> None:
        self._devices: dict[str, HomeDevice] = {}
        self._lock = threading.RLock()
        for item in configured:
            self.register(HomeDevice(str(item["device_id"]), str(item["name"]), str(item["type"]),
                DeviceStatus.OFFLINE, tuple(item["capabilities"]), str(item.get("node_id", "home.gateway")),
                DeviceRisk(item["risk_level"]), metadata=dict(item.get("metadata", {})), simulated=bool(item.get("simulated", False))))

    def register(self, device: HomeDevice) -> HomeDevice:
        if not DEVICE_ID.fullmatch(device.device_id) or not device.capabilities or set(device.capabilities) - ALLOWED_CAPABILITIES:
            raise ValueError("Home device identity or capabilities are invalid")
        if "relay" in device.capabilities and device.risk_level is DeviceRisk.SAFE:
            raise ValueError("A generic relay cannot be classified SAFE")
        with self._lock:
            if device.device_id in self._devices: raise ValueError(f"Device already registered: {device.device_id}")
            self._devices[device.device_id] = device
        return device

    def get(self, device_id: str) -> HomeDevice | None:
        with self._lock: return self._devices.get(device_id)

    def require(self, device_id: str) -> HomeDevice:
        device = self.get(device_id)
        if not device: raise PermissionError("Device is not registered in the Home allowlist")
        return device

    def list(self) -> list[HomeDevice]:
        with self._lock: return list(self._devices.values())


class VirtualDeviceAdapter:
    def __init__(self, registry: DeviceRegistry) -> None:
        self.registry = registry
        self.states: dict[str, dict[str, Any]] = {}

    def start(self) -> None:
        for device in self.registry.list():
            if not device.simulated: continue
            device.status = DeviceStatus.ONLINE; device.last_seen = now_iso()
            if "light" in device.capabilities: self.states[device.device_id] = {"on": False}
            if "temperature" in device.capabilities: self.states[device.device_id] = {"temperature": 23.4, "unit": "celsius"}

    def command(self, device: HomeDevice, command: str) -> dict[str, Any]:
        if not device.simulated or device.status is not DeviceStatus.ONLINE: raise ConnectionError("Device is offline")
        state = self.states.setdefault(device.device_id, {})
        if command in {"light_on", "light_off"} and "light" in device.capabilities:
            state["on"] = command == "light_on"; return {"on": state["on"]}
        if command == "get_temperature" and "temperature" in device.capabilities:
            return {"value": state["temperature"], "unit": state["unit"]}
        if command == "get_status": return dict(state)
        raise ValueError("Command is not supported by this device")
