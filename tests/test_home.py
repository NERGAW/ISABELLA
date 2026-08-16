from datetime import datetime, timedelta, timezone
import pytest
from Isabella.Automations import AutomationManager
from Isabella.Events import Event, EventType
from Isabella.Home import HomeManager
from Isabella.Home.devices import DeviceRegistry
from Isabella.Home.models import DeviceStatus
from Isabella.Security import SecurityPolicyEngine
from Isabella.Skills.base import RiskLevel, SkillDefinition, SkillResult
from Isabella.Skills.home import create_home_skills
from Isabella.Skills.registry import SkillRegistry

class Bus:
    def __init__(self): self.events = []; self.subscribers = set()
    def emit(self, kind, source, payload=None, **kwargs): self.events.append((getattr(kind, "value", kind), payload or {})); return True
    def subscribe(self, pattern, callback): self.subscribers.add(callback)
    def unsubscribe(self, pattern, callback): self.subscribers.discard(callback); return True

class Context:
    def __init__(self): self.metadata = {}
    def get(self, name, default=None): return self.metadata if name == "metadata" else default
    def set(self, name, value): self.metadata = value

def config(devices=None, mqtt=False):
    return {"enabled": True, "gateway_node_id": "home.gateway", "offline_after_seconds": 30,
        "mqtt": {"enabled": mqtt, "host": "127.0.0.1", "port": 1883, "authentication_required": True, "username_env": "TEST_MQTT_USER", "password_env": "TEST_MQTT_PASS", "tls": False},
        "devices": devices or [{"device_id": "virtual_light_1", "name": "Light", "type": "VIRTUAL_LIGHT", "capabilities": ["light", "switch"], "risk_level": "CAUTION", "simulated": True}, {"device_id": "virtual_temperature_sensor", "name": "Temp", "type": "VIRTUAL_SENSOR", "capabilities": ["temperature"], "risk_level": "SAFE", "simulated": True}]}

def test_virtual_registration_telemetry_light_and_context():
    bus, context = Bus(), Context(); home = HomeManager(config(), event_bus=bus, context=context)
    assert home.start(); assert home.command("virtual_light_1", "light_on") == {"on": True}; assert home.command("virtual_light_1", "light_off") == {"on": False}
    assert home.command("virtual_temperature_sensor", "get_temperature")["value"] == 23.4
    telemetry = home.ingest_telemetry("virtual_temperature_sensor", {"capability": "temperature", "value": 30.5, "unit": "celsius", "timestamp": datetime.now(timezone.utc).isoformat()})
    assert telemetry.value == 30.5 and "virtual_light_1" in context.metadata["home_devices_online"]
    assert EventType.HOME_TELEMETRY.value in {item[0] for item in bus.events}

def test_unknown_offline_invalid_payload_mqtt_disconnect_and_relay_safety():
    home = HomeManager(config()); home.start()
    with pytest.raises(PermissionError): home.command("unknown", "light_on")
    home.registry.get("virtual_light_1").status = DeviceStatus.OFFLINE
    with pytest.raises(ConnectionError): home.command("virtual_light_1", "light_on")
    with pytest.raises(ValueError): home.ingest_telemetry("virtual_temperature_sensor", {"value": {"bad": True}})
    assert home.health_check()["telemetry_errors"] == 1
    home.mqtt.config["enabled"] = True; home.mqtt.connected = False; assert home.health_check()["broker"] == "OFFLINE"
    with pytest.raises(ValueError, match="relay"): DeviceRegistry([{"device_id": "unsafe_relay", "name": "Unsafe", "type": "RELAY", "capabilities": ["relay"], "risk_level": "SAFE"}])
    critical = HomeManager(config([{"device_id": "critical_light", "name": "Critical", "type": "LIGHT", "capabilities": ["light"], "risk_level": "CRITICAL", "simulated": True}]))
    critical.start()
    with pytest.raises(PermissionError, match="Critical"): critical.command("critical_light", "light_on")

def test_heartbeat_timeout_and_home_skill_security_denial():
    home = HomeManager(config([{"device_id": "sensor_lab", "name": "Sensor", "type": "SENSOR", "capabilities": ["temperature"], "risk_level": "SAFE"}]))
    device = home.gateway.heartbeat("sensor_lab"); device.last_seen = (datetime.now(timezone.utc) - timedelta(seconds=31)).isoformat(); assert home.gateway.mark_offline() == ["sensor_lab"]
    policy = SecurityPolicyEngine({"confirmation_timeout_seconds": 30, "risk_policies": {"SAFE": "ALLOW", "CAUTION": "DENY", "CRITICAL": "CONFIRM"}, "critical_confirmation_required": True, "logging_level": "INFO"})
    registry = SkillRegistry(policy_engine=policy)
    for skill in create_home_skills(HomeManager(config())): registry.register(skill)
    assert registry.execute("home.light_on", {"device_id": "virtual_light_1"}).status == "denied"
    assert registry.get("home.execute_raw_command") is None

def test_home_event_drives_safe_automation_notification_action(tmp_path):
    executions = []; policy = SecurityPolicyEngine({"confirmation_timeout_seconds": 30, "risk_policies": {"SAFE": "ALLOW", "CAUTION": "ALLOW", "CRITICAL": "CONFIRM"}, "critical_confirmation_required": True, "logging_level": "INFO"})
    registry = SkillRegistry(policy_engine=policy); registry.register(SkillDefinition("test.notify", "Notify", "test", "test", {}, RiskLevel.SAFE, lambda args: (executions.append("notified") or SkillResult(True, "test.notify", "notified"))))
    bus = Bus(); manager = AutomationManager({"enabled": True, "database_path": str(tmp_path / "home.db"), "default_cooldown_seconds": 0, "max_chain_depth": 5, "max_actions": 8, "max_action_retries": 0}, registry=registry, event_bus=bus)
    manager.create_automation({"id": "home.hot", "name": "Hot warning", "enabled": True, "trigger": {"type": "EVENT", "event": "home.telemetry"}, "conditions": [{"field": "value", "operator": "greater_than", "value": 30}], "actions": [{"skill": "test.notify", "arguments": {}}], "owner": "user", "source": "manual_structured", "cooldown_seconds": 0})
    manager.engine.handle_event(Event("home.telemetry", "home", {"value": 31}, "hot")); assert executions == ["notified"]

def test_telemetry_stress_is_bounded_to_latest_per_sensor():
    devices = [{"device_id": f"sensor_{i}", "name": f"Sensor {i}", "type": "VIRTUAL_SENSOR", "capabilities": ["temperature"], "risk_level": "SAFE", "simulated": True} for i in range(40)]
    home = HomeManager(config(devices)); home.start(); stamp = datetime.now(timezone.utc).isoformat()
    for cycle in range(50):
        for index in range(40): home.ingest_telemetry(f"sensor_{index}", {"capability": "temperature", "value": 20 + cycle / 10, "unit": "celsius", "timestamp": stamp})
    assert len(home.gateway.latest_telemetry) == 40

def test_mqtt_rejects_public_broker_and_requires_environment_credentials(monkeypatch):
    public = HomeManager(config(mqtt=True)); public.mqtt.config["host"] = "8.8.8.8"
    with pytest.raises(ValueError, match="Public"): public.mqtt.start()
    local = HomeManager(config(mqtt=True)); monkeypatch.delenv("TEST_MQTT_USER", raising=False); monkeypatch.delenv("TEST_MQTT_PASS", raising=False)
    with pytest.raises(RuntimeError, match="credentials"): local.mqtt.start()
