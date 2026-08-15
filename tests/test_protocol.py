import json

import pytest

from Isabella.Protocol import (
    MAX_MESSAGE_BYTES, MessageType, NodeIdentity, NodeType, ProtocolMessage,
    ProtocolValidationError, decode, dispatch_command, encode, negotiate_hello,
)
from Isabella.Security import SecurityPolicyEngine
from Isabella.Skills.base import ParameterSpec, RiskLevel, SkillDefinition, SkillResult
from Isabella.Skills.registry import SkillRegistry


CAPABILITIES = {"notifications", "skill_execution", "sensors"}


def identity(node_id="phone.1", version="1.0", capabilities=("notifications",)):
    return NodeIdentity(node_id, NodeType.SMARTPHONE, "Phone", version, capabilities)


def message(kind, payload=None, **changes):
    values = {"type": kind, "source": "phone.1", "destination": "primary.1", "payload": payload or {}}
    values.update(changes)
    return ProtocolMessage(**values)


def registry(executions=None):
    executions = executions if executions is not None else []
    policy = SecurityPolicyEngine({"confirmation_timeout_seconds": 30, "risk_policies": {"SAFE": "ALLOW", "CAUTION": "ALLOW", "CRITICAL": "CONFIRM"}, "critical_confirmation_required": True, "logging_level": "INFO"})
    result = SkillRegistry(policy_engine=policy)
    result.register(SkillDefinition("test.safe", "Safe", "test", "test", {"value": ParameterSpec(int)}, RiskLevel.SAFE, lambda args: (executions.append(args) or SkillResult(True, "test.safe", "ok"))))
    result.register(SkillDefinition("system.shutdown", "Shutdown", "test", "system", {}, RiskLevel.CRITICAL, lambda args: SkillResult(True, "system.shutdown", "must not execute")))
    return result


def test_hello_json_round_trip_and_welcome_negotiation():
    hello = message(MessageType.HELLO, {"identity": identity().to_dict()})
    encoded = encode(hello, available_capabilities=CAPABILITIES)
    decoded = decode(encoded, available_capabilities=CAPABILITIES)
    assert decoded == hello
    primary = NodeIdentity("primary.1", NodeType.PRIMARY, "ISABELLA", capabilities=("skill_execution",))
    welcome = negotiate_hello(hello, primary, primary_capabilities=CAPABILITIES, peer_capabilities=CAPABILITIES)
    assert welcome.type is MessageType.WELCOME
    assert welcome.payload["accepted_version"] == "1.0"
    assert welcome.payload["heartbeat_seconds"] == 15
    assert decode(encode(welcome, available_capabilities=CAPABILITIES), available_capabilities=CAPABILITIES).type is MessageType.WELCOME


def test_invalid_version_returns_structured_error_and_does_not_continue():
    hello = message(MessageType.HELLO, {"identity": identity(version="2.0").to_dict()}, protocol_version="2.0")
    primary = NodeIdentity("primary.1", NodeType.PRIMARY, "ISABELLA")
    response = negotiate_hello(hello, primary, primary_capabilities=set(), peer_capabilities=set())
    assert response.type is MessageType.ERROR
    assert response.payload["code"] == "INCOMPATIBLE_VERSION"
    assert response.payload["request_id"] == hello.id
    with pytest.raises(ProtocolValidationError, match="not supported"):
        encode(hello, available_capabilities=CAPABILITIES)


def test_unknown_type_and_invalid_payload_are_rejected():
    raw = message(MessageType.HEARTBEAT, {"status": "ONLINE"}).to_dict()
    raw["type"] = "EXECUTE_PYTHON"
    with pytest.raises(ProtocolValidationError, match="Envelope values"):
        decode(json.dumps(raw))
    invalid = message(MessageType.HEARTBEAT, {"status": "ONLINE", "aggressive_interval": 0})
    with pytest.raises(ProtocolValidationError, match="unknown fields"):
        encode(invalid)


def test_heartbeat_status_and_telemetry_are_structured():
    heartbeat = message(MessageType.HEARTBEAT, {"status": "ONLINE", "sequence": 4})
    assert decode(encode(heartbeat)).payload["sequence"] == 4
    status = message(MessageType.STATUS, {"status": "DEGRADED", "details": {"battery": 20}})
    assert decode(encode(status)).payload["status"] == "DEGRADED"
    telemetry = message(MessageType.TELEMETRY, {"capability": "sensors", "metrics": {"temperature_c": 24.5}})
    assert decode(encode(telemetry, available_capabilities=CAPABILITIES), available_capabilities=CAPABILITIES).payload["metrics"]["temperature_c"] == 24.5
    wrong = message(MessageType.TELEMETRY, {"capability": "gps", "metrics": {"latitude": 0}})
    with pytest.raises(ProtocolValidationError, match="not authorized"):
        encode(wrong, available_capabilities=CAPABILITIES)


def test_oversized_nested_and_embedded_image_messages_are_rejected():
    oversized = message(MessageType.STATUS, {"status": "ONLINE", "text": "x" * MAX_MESSAGE_BYTES})
    with pytest.raises(ProtocolValidationError) as captured:
        encode(oversized)
    assert captured.value.code == "MESSAGE_TOO_LARGE"
    image = message(MessageType.STATUS, {"status": "ONLINE", "image_base64": "abc"})
    with pytest.raises(ProtocolValidationError) as captured:
        encode(image)
    assert captured.value.code == "EMBEDDED_BINARY_FORBIDDEN"
    nested = value = {}
    for _ in range(20):
        value["next"] = {}
        value = value["next"]
    with pytest.raises(ProtocolValidationError) as captured:
        encode(message(MessageType.STATUS, {"status": "ONLINE", "details": nested}))
    assert captured.value.code == "PAYLOAD_TOO_DEEP"


def test_authenticated_command_uses_registry_and_security():
    executions = []
    skills = registry(executions)
    command = message(MessageType.COMMAND_REQUEST, {"skill_id": "test.safe", "arguments": {"value": 7}})
    result = dispatch_command(command, authenticated=True, registry=skills)
    assert result.success and executions == [{"value": 7}]
    with pytest.raises(ProtocolValidationError) as captured:
        dispatch_command(command, authenticated=False, registry=skills)
    assert captured.value.code == "AUTHENTICATION_REQUIRED"


def test_critical_unknown_shell_and_permission_bypass_all_fail():
    skills = registry()
    critical = message(MessageType.COMMAND_REQUEST, {"skill_id": "system.shutdown", "arguments": {}})
    result = dispatch_command(critical, authenticated=True, registry=skills)
    assert result.status == "confirmation_required" and not result.success
    unknown = message(MessageType.COMMAND_REQUEST, {"skill_id": "unknown.skill", "arguments": {}})
    assert dispatch_command(unknown, authenticated=True, registry=skills).error_code == "UNKNOWN_SKILL"
    for payload in (
        {"skill_id": "test.safe", "arguments": {"value": 1}, "shell": "whoami"},
        {"skill_id": "test.safe", "arguments": {"value": 1}, "permissions": ["admin"]},
        {"skill_id": "test.safe", "arguments": {"value": 1}, "confirmed": True},
    ):
        with pytest.raises(ProtocolValidationError) as captured:
            dispatch_command(message(MessageType.COMMAND_REQUEST, payload), authenticated=True, registry=skills)
        assert captured.value.code == "INVALID_COMMAND"


def test_events_require_explicit_export_allowlist():
    event = message(MessageType.EVENT, {"event": "diagnostics.status_changed", "data": {"status": "OFFLINE"}})
    with pytest.raises(ProtocolValidationError, match="not explicitly authorized"):
        encode(event)
    encoded = encode(event, authorized_events={"diagnostics.status_changed"})
    assert decode(encoded, authorized_events={"diagnostics.status_changed"}).type is MessageType.EVENT


def test_identity_cannot_announce_nonexistent_capability_or_mismatch_source():
    fake = message(MessageType.HELLO, {"identity": identity(capabilities=("camera",)).to_dict()})
    with pytest.raises(ProtocolValidationError) as captured:
        encode(fake, available_capabilities=CAPABILITIES)
    assert captured.value.code == "UNAVAILABLE_CAPABILITY"
    mismatch = message(MessageType.HELLO, {"identity": identity(node_id="other.1").to_dict()})
    with pytest.raises(ProtocolValidationError) as captured:
        encode(mismatch, available_capabilities=CAPABILITIES)
    assert captured.value.code == "IDENTITY_MISMATCH"
