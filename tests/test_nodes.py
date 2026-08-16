from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys

import pytest

from Isabella.Events import EventType
from Isabella.Nodes import Node, NodeManager, NodeRegistry, NodeStatus, NodeType, TrustState, load_or_create_node_id
from Isabella.Protocol import MessageType, PROTOCOL_VERSION, decode, encode


class Bus:
    def __init__(self):
        self.events = []

    def emit(self, event_type, source, payload=None, **kwargs):
        self.events.append(event_type.value if hasattr(event_type, "value") else event_type)
        return True


class Context:
    def __init__(self):
        self.values = {}

    def update(self, **values):
        self.values.update(values)


class Vision:
    def __init__(self, camera=False):
        self.camera = camera

    def health_check(self, check_camera=False):
        return {"screen": True, "camera": self.camera if check_camera else None}


class LLM:
    def health_check(self):
        return True


class Research:
    def health_check(self):
        return {"provider_configured": True}


class Controller:
    def __init__(self):
        self.nodes_status = None

    def update_subsystem(self, name, status):
        if name == "NODES":
            self.nodes_status = status


def config(tmp_path):
    return {"enabled": True, "identity_file": str(tmp_path / "identity.json"), "registry_file": str(tmp_path / "registry.json"), "offline_after_seconds": 30, "known_capabilities": ["text_input", "voice_input", "voice_output", "screen_capture", "camera_capture", "local_llm", "skill_execution", "hud", "memory", "research", "notifications", "sensors", "display", "imu"]}


def manager(tmp_path, *, camera=False, bus=None, context=None):
    app = type("App", (), {"voice_listener": object(), "tts_manager": object()})()
    brain = type("Brain", (), {"vision": Vision(camera), "llm": LLM(), "registry": object(), "memory": object(), "research": Research(), "context": context})()
    return NodeManager(config(tmp_path), app=app, brain=brain, controller=Controller(), context=context, event_bus=bus, registry=NodeRegistry(tmp_path / "registry.json"))


def mobile(node_id="mobile.test", protocol=PROTOCOL_VERSION, trust=TrustState.UNTRUSTED):
    return Node(node_id, "Test Mobile", NodeType.MOBILE, NodeStatus.CONNECTING, protocol, ("notifications",), trust=trust)


def test_primary_registration_persistent_id_context_and_events(tmp_path):
    bus, context = Bus(), Context()
    first = manager(tmp_path, bus=bus, context=context)
    assert first.start()
    primary = first.primary()
    assert primary.node_type is NodeType.PRIMARY_PC
    assert primary.status is NodeStatus.ONLINE and primary.trust is TrustState.TRUSTED
    assert primary.node_id == load_or_create_node_id(tmp_path / "identity.json")
    assert context.values["primary_node"] == primary.node_id
    assert context.values["active_nodes"] == (primary.node_id,)
    assert EventType.NODE_REGISTERED.value in bus.events and EventType.NODE_ONLINE.value in bus.events
    second = manager(tmp_path)
    second.start()
    assert second.primary_node_id == primary.node_id


def test_capabilities_are_detected_without_claiming_missing_camera(tmp_path):
    nodes = manager(tmp_path, camera=False)
    nodes.start()
    capabilities = set(nodes.primary().capabilities)
    assert {"text_input", "voice_input", "voice_output", "screen_capture", "local_llm", "skill_execution", "hud", "memory", "research"} <= capabilities
    assert "camera_capture" not in capabilities
    with_camera = manager(tmp_path / "camera", camera=True)
    with_camera.start()
    assert "camera_capture" in with_camera.primary().capabilities


def test_primary_hello_is_protocol_v1_compatible(tmp_path):
    nodes = manager(tmp_path)
    nodes.start()
    hello = nodes.hello()
    assert hello.type is MessageType.HELLO and hello.protocol_version == "1.0"
    available = set(nodes.primary().capabilities)
    assert decode(encode(hello, available_capabilities=available), available_capabilities=available).payload["identity"]["node_type"] == "PRIMARY"


def test_simulated_mobile_is_untrusted_and_duplicate_rejected(tmp_path):
    bus = Bus()
    nodes = manager(tmp_path, bus=bus)
    nodes.start()
    fake = mobile(trust=TrustState.TRUSTED)
    nodes.register(fake)
    assert fake.status is NodeStatus.ONLINE and fake.trust is TrustState.UNTRUSTED
    assert EventType.NODE_DISCOVERED.value in bus.events
    with pytest.raises(ValueError, match="already registered"):
        nodes.register(mobile())


def test_heartbeat_offline_detection_and_revoke(tmp_path):
    bus = Bus()
    nodes = manager(tmp_path, bus=bus)
    nodes.start()
    fake = nodes.register(mobile())
    old = datetime.now(timezone.utc) - timedelta(seconds=31)
    fake.last_seen = old.isoformat()
    nodes.registry.save(fake)
    assert nodes.mark_offline(datetime.now(timezone.utc))[0].status is NodeStatus.OFFLINE
    assert nodes.heartbeat(fake.node_id).status is NodeStatus.ONLINE
    assert nodes.revoke(fake.node_id).trust is TrustState.REVOKED
    with pytest.raises(PermissionError):
        nodes.heartbeat(fake.node_id)
    assert EventType.NODE_OFFLINE.value in bus.events and EventType.NODE_REVOKED.value in bus.events


def test_invalid_protocol_capability_and_primary_impersonation_fail(tmp_path):
    nodes = manager(tmp_path)
    nodes.start()
    with pytest.raises(ValueError, match="incompatible"):
        nodes.register(mobile("mobile.v2", protocol="2.0"))
    fake = mobile("mobile.fake_cap")
    fake.capabilities = ("teleportation",)
    with pytest.raises(Exception, match="unavailable capability"):
        nodes.register(fake)
    impersonator = Node("primary.attacker", "Attacker", NodeType.PRIMARY_PC, NodeStatus.CONNECTING, capabilities=("text_input",))
    with pytest.raises(ValueError, match="local persistent"):
        nodes.register(impersonator)


def test_registry_persists_known_nodes_and_diagnostics(tmp_path):
    nodes = manager(tmp_path)
    nodes.start()
    nodes.register(mobile())
    details = nodes.diagnostics()
    assert details["nodes_total"] == 2 and details["online"] == 2
    restored = NodeRegistry(tmp_path / "registry.json")
    assert {item.node_id for item in restored.list()} == {nodes.primary_node_id, "mobile.test"}


def test_simulator_cli_has_no_access_grant():
    result = subprocess.run([sys.executable, "tools/simulate_node.py", "--type", "MOBILE"], capture_output=True, text=True, timeout=10, check=True)
    assert "TYPE=MOBILE" in result.stdout
    assert "STATUS=ONLINE" in result.stdout
    assert "TRUST=UNTRUSTED" in result.stdout
    assert "PROTOCOL=1.0" in result.stdout
    assert "ACCESS_GRANTED=false" in result.stdout
