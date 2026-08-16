"""Optional standards-based MQTT adapter; no broker is embedded or exposed."""

import ipaddress
import json
import os
import socket
from typing import Any, Callable


class MQTTAdapter:
    def __init__(self, config: dict[str, Any], on_telemetry: Callable, on_heartbeat: Callable) -> None:
        self.config = config; self.on_telemetry = on_telemetry; self.on_heartbeat = on_heartbeat
        self.client = None; self.connected = False; self.errors = 0

    def start(self) -> bool:
        if not self.config.get("enabled", False): return True
        host = self.config["host"]
        try:
            address = ipaddress.ip_address(socket.gethostbyname(host))
        except (ValueError, OSError) as exc: raise ValueError("MQTT broker address is invalid") from exc
        if not (address.is_loopback or address.is_private): raise ValueError("Public MQTT brokers are forbidden")
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc: raise RuntimeError("paho-mqtt is required when MQTT is enabled") from exc
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="isabella-home-gateway")
        username = os.getenv(self.config.get("username_env", "ISABELLA_MQTT_USERNAME"))
        password = os.getenv(self.config.get("password_env", "ISABELLA_MQTT_PASSWORD"))
        if self.config.get("authentication_required", True) and (not username or not password):
            raise RuntimeError("MQTT credentials are required through environment variables")
        if username: self.client.username_pw_set(username, password)
        if self.config.get("tls", False): self.client.tls_set()
        self.client.on_connect = self._on_connect; self.client.on_disconnect = self._on_disconnect; self.client.on_message = self._on_message
        self.client.connect_async(host, int(self.config["port"]), keepalive=30); self.client.loop_start(); return True

    def publish_command(self, device_id: str, command: str) -> bool:
        if not self.connected or not self.client: return False
        result = self.client.publish(f"isabella/home/{device_id}/command", json.dumps({"command": command}), qos=1)
        return result.rc == 0

    def shutdown(self) -> bool:
        if self.client:
            self.client.disconnect(); self.client.loop_stop()
        self.connected = False; return True

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        self.connected = int(reason_code) == 0
        if self.connected:
            client.subscribe("isabella/home/+/telemetry", qos=1); client.subscribe("isabella/home/+/heartbeat", qos=1)

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties): self.connected = False

    def _on_message(self, client, userdata, message):
        try:
            parts = message.topic.split("/"); device_id, kind = parts[2], parts[3]
            payload = json.loads(message.payload.decode("utf-8"))
            if not isinstance(payload, dict): raise ValueError
            (self.on_heartbeat if kind == "heartbeat" else self.on_telemetry)(device_id, payload)
        except Exception: self.errors += 1
