import json
from pathlib import Path
import subprocess
import sys
from time import sleep

import pytest
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect as sync_connect

from Isabella.API.auth import RateLimiter, TokenAuthentication
from Isabella.Events import EventBus
from Isabella.Nodes import NodeManager, NodeRegistry, NodeStatus, TrustState
from Isabella.Protocol import MessageType, NodeIdentity, NodeType, ProtocolMessage
from Isabella.Security import SecurityPolicyEngine
from Isabella.Security.Devices import DeviceIdentity, DevicePairingManager
from Isabella.Skills.base import RiskLevel, SkillDefinition, SkillResult
from Isabella.Skills.registry import SkillRegistry
from Isabella.Transport import TransportManager, WebSocketNodeClient, WebSocketNodeServer


def node_config(tmp_path):
    return {"enabled": True, "identity_file": str(tmp_path / "identity.json"), "registry_file": str(tmp_path / "nodes.json"), "offline_after_seconds": 30, "known_capabilities": ["text_input", "skill_execution", "notifications", "sensors"]}


def registry(executions=None):
    executions = executions if executions is not None else []
    policy = SecurityPolicyEngine({"confirmation_timeout_seconds": 30, "risk_policies": {"SAFE": "ALLOW", "CAUTION": "ALLOW", "CRITICAL": "CONFIRM"}, "critical_confirmation_required": True, "logging_level": "INFO"})
    result = SkillRegistry(policy_engine=policy)
    result.register(SkillDefinition("test.safe", "Safe", "test", "test", {}, RiskLevel.SAFE, lambda args: (executions.append("safe") or SkillResult(True, "test.safe", "ok"))))
    result.register(SkillDefinition("system.shutdown", "Shutdown", "test", "system", {}, RiskLevel.CRITICAL, lambda args: SkillResult(True, "system.shutdown", "must not run")))
    result.register(SkillDefinition("scheduler.list", "List", "test", "scheduler", {}, RiskLevel.SAFE, lambda args: SkillResult(True, "scheduler.list", "listed")))
    return result


def make_nodes(tmp_path, skills):
    brain = type("Brain", (), {"vision": None, "llm": None, "registry": skills, "memory": None, "research": None, "context": None})()
    nodes = NodeManager(node_config(tmp_path), brain=brain, registry=NodeRegistry())
    nodes.start()
    return nodes


def ws_config(tmp_path, **changes):
    result = {"enabled": True, "host": "127.0.0.1", "port": 0, "allow_remote": False, "heartbeat_seconds": 5, "connection_timeout_seconds": 5, "max_message_size": 65536, "token_file": str(tmp_path / "token.txt"), "rate_limit": 100, "rate_window_seconds": 60, "event_allowlist": ["diagnostics.status_changed"]}
    result.update(changes)
    return result


def server(tmp_path, executions=None, **changes):
    skills = registry(executions)
    nodes = make_nodes(tmp_path, skills)
    config = ws_config(tmp_path, **changes)
    auth = TokenAuthentication(Path(config["token_file"]), True)
    transport = WebSocketNodeServer(config, node_manager=nodes, registry=skills, authentication=auth, rate_limiter=RateLimiter(config["rate_limit"], config["rate_window_seconds"]))
    assert transport.start()
    return transport, nodes, auth


def client(transport, node_id="mobile.test", token=None, authorized_events=None):
    identity = NodeIdentity(node_id, NodeType.SMARTPHONE, node_id, capabilities=("notifications",))
    return WebSocketNodeClient(f"ws://127.0.0.1:{transport.port}", identity, token=token, timeout=5, available_capabilities={"text_input", "skill_execution", "notifications", "sensors"}, authorized_events=authorized_events, max_backoff_seconds=.01)


def test_connect_welcome_heartbeat_status_goodbye(tmp_path):
    transport, nodes, _ = server(tmp_path)
    mobile = client(transport)
    assert mobile.connect().type is MessageType.WELCOME
    assert mobile.heartbeat(7).payload["sequence"] == 7
    mobile.send(ProtocolMessage(MessageType.STATUS, "mobile.test", "primary.local", {"status": "ONLINE"}))
    assert nodes.get("mobile.test").trust is TrustState.UNTRUSTED
    assert mobile.disconnect()
    sleep(.05)
    assert nodes.get("mobile.test").status is NodeStatus.DISCONNECTED
    assert transport.shutdown()


def test_authenticated_command_and_security_denial(tmp_path):
    executions = []
    transport, _, auth = server(tmp_path, executions)
    authenticated = client(transport, token=auth.token_for_local_setup)
    authenticated.connect()
    request = ProtocolMessage(MessageType.COMMAND_REQUEST, "mobile.test", "primary.local", {"skill_id": "test.safe", "arguments": {}})
    authenticated.send(request)
    result = authenticated.receive()
    assert result.type is MessageType.COMMAND_RESULT and result.payload["success"]
    assert result.payload["request_id"] == request.id and executions == ["safe"]
    critical = ProtocolMessage(MessageType.COMMAND_REQUEST, "mobile.test", "primary.local", {"skill_id": "system.shutdown", "arguments": {}})
    authenticated.send(critical)
    assert authenticated.receive().payload["status"] == "confirmation_required"
    authenticated.disconnect()
    unauthenticated = client(transport, "mobile.unauth")
    unauthenticated.connect()
    unauthenticated.send(ProtocolMessage(MessageType.COMMAND_REQUEST, "mobile.unauth", "primary.local", {"skill_id": "test.safe", "arguments": {}}))
    denied = unauthenticated.receive()
    assert denied.payload["status"] == "denied" and denied.payload["error"] == "AUTHENTICATION_REQUIRED"
    unauthenticated.disconnect()
    transport.shutdown()


def test_reconnect_and_duplicate_active_connection(tmp_path):
    transport, _, _ = server(tmp_path)
    first = client(transport)
    first.connect()
    duplicate = client(transport)
    with pytest.raises(ConnectionError, match="DUPLICATE_CONNECTION"):
        duplicate.connect()
    duplicate.disconnect()
    assert first.reconnect().type is MessageType.WELCOME
    assert first.reconnects == 1
    first.disconnect()
    transport.shutdown()


def test_malformed_invalid_version_and_oversized_are_closed(tmp_path):
    transport, _, _ = server(tmp_path)
    uri = f"ws://127.0.0.1:{transport.port}"
    raw = sync_connect(uri, max_size=2**20)
    raw.send("not-json")
    error = json.loads(raw.recv())
    assert error["type"] == "ERROR"
    raw.close()
    invalid = sync_connect(uri)
    identity = NodeIdentity("mobile.v2", NodeType.SMARTPHONE, "V2", protocol_version="2.0", capabilities=("notifications",))
    hello = ProtocolMessage(MessageType.HELLO, "mobile.v2", "primary.local", {"identity": identity.to_dict()}, protocol_version="2.0")
    invalid.send(json.dumps(hello.to_dict()))
    assert json.loads(invalid.recv())["payload"]["code"] == "INCOMPATIBLE_VERSION"
    invalid.close()
    huge = sync_connect(uri, max_size=2**20)
    huge.send("x" * 70000)
    with pytest.raises(ConnectionClosed):
        while True:
            huge.recv()
    assert transport.diagnostics()["errors"] >= 2
    transport.shutdown()


def test_heartbeat_timeout_marks_offline(tmp_path):
    transport, nodes, _ = server(tmp_path)
    transport.heartbeat_timeout_seconds = .05
    mobile = client(transport)
    mobile.connect()
    sleep(.25)
    assert nodes.get("mobile.test").status in {NodeStatus.OFFLINE, NodeStatus.DISCONNECTED}
    mobile.disconnect()
    transport.shutdown()


def test_unknown_node_registers_untrusted_and_revoked_reconnect_fails(tmp_path):
    transport, nodes, _ = server(tmp_path)
    unknown = client(transport, "mobile.unknown")
    unknown.connect()
    unknown.disconnect()
    assert nodes.get("mobile.unknown").trust is TrustState.UNTRUSTED
    nodes.revoke("mobile.unknown")
    rejected = client(transport, "mobile.unknown")
    with pytest.raises(ConnectionError):
        rejected.connect()
    rejected.disconnect()
    transport.shutdown()


def test_multiple_local_clients_stress(tmp_path):
    transport, _, _ = server(tmp_path)
    clients = [client(transport, f"mobile.stress{i}") for i in range(7)]
    for item in clients:
        item.connect()
    assert transport.diagnostics()["connections"] == 7
    for index, item in enumerate(clients):
        assert item.heartbeat(index).type is MessageType.HEARTBEAT
    for item in clients:
        item.disconnect()
    assert transport.shutdown()


def test_event_allowlist_and_transport_manager_diagnostics(tmp_path):
    bus = EventBus({"enabled": True, "queue_max_size": 100, "worker_count": 1, "high_priority_reserve": 10, "shutdown_timeout_seconds": 2})
    skills = registry()
    nodes = make_nodes(tmp_path, skills)
    config = {"websocket": ws_config(tmp_path)}
    manager = TransportManager(config, node_manager=nodes, registry=skills, event_bus=bus)
    assert manager.start()
    mobile = client(manager.server, authorized_events={"diagnostics.status_changed"})
    mobile.connect()
    bus.emit("unlisted.event", "test", {"secret": False})
    bus.emit("diagnostics.status_changed", "test", {"subsystem": "LLM", "status": "OFFLINE"}, correlation_id="event-flow")
    event = mobile.receive(timeout=2)
    assert event.type is MessageType.EVENT and event.payload["event"] == "diagnostics.status_changed"
    details = manager.diagnostics()
    assert details["connections"] == 1 and details["messages_sent"] >= 2
    mobile.disconnect()
    manager.shutdown()
    bus.shutdown()


def test_simulated_node_cli_connects_over_real_websocket(tmp_path):
    transport, _, auth = server(tmp_path)
    result = subprocess.run(
        [sys.executable, "tools/simulate_node.py", "--type", "MOBILE", "--connect", f"ws://127.0.0.1:{transport.port}", "--token-file", str(auth.token_path)],
        capture_output=True, text=True, timeout=15, check=True,
    )
    assert "WELCOME=WELCOME" in result.stdout
    assert "HEARTBEAT=HEARTBEAT" in result.stdout
    assert "COMMAND=completed" in result.stdout
    assert "GOODBYE=SENT" in result.stdout
    transport.shutdown()


def test_secure_device_pairing_signed_handshake_and_permission(tmp_path):
    skills = registry([])
    nodes = make_nodes(tmp_path, skills)
    device_config = {"pairing_enabled_by_default": False, "pairing_window_seconds": 30,
                     "code_ttl_seconds": 20, "credential_registry_file": str(tmp_path / "devices.json"),
                     "replay_window_seconds": 60, "default_permissions": ["send_commands"]}
    devices = DevicePairingManager(device_config)
    identity_key = DeviceIdentity.load_or_create("mobile.secure", tmp_path / "mobile.pem")
    devices.start_pairing()
    pairing = devices.request_pairing("mobile.secure", identity_key.public_identity, ("send_commands",))
    assert devices.verify_code(pairing.pairing_id, pairing.display_code)
    devices.approve(pairing.pairing_id)
    config = ws_config(tmp_path)
    auth = TokenAuthentication(Path(config["token_file"]), True)
    transport = WebSocketNodeServer(config, node_manager=nodes, registry=skills, authentication=auth,
                                    rate_limiter=RateLimiter(100, 60), device_security=devices)
    assert transport.start()
    identity = NodeIdentity("mobile.secure", NodeType.SMARTPHONE, "Secure", capabilities=("notifications",))
    mobile = WebSocketNodeClient(f"ws://127.0.0.1:{transport.port}", identity, device_identity=identity_key,
                                 timeout=5, available_capabilities={"text_input", "skill_execution", "notifications", "sensors"})
    assert mobile.connect().type is MessageType.WELCOME
    request = ProtocolMessage(MessageType.COMMAND_REQUEST, "mobile.secure", "primary.local", {"skill_id": "test.safe", "arguments": {}})
    mobile.send(request)
    assert mobile.receive().payload["success"]
    mobile.disconnect()
    assert transport.shutdown()
