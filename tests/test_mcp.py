from time import sleep
from pathlib import Path
import sys

import pytest

from Isabella.Events import EventType
from Isabella.MCP import MCPClient, MCPManager, MCPServer, MCPServerState, MCPTool, MCPToolResult, MCPTransport, load_mcp_config
from Isabella.Core.config import ConfigurationError
from Isabella.Security import SecurityPolicyEngine
from Isabella.Skills import RiskLevel, SkillRegistry


CONFIG = {
    "enabled": True, "servers": [], "connection_timeout_seconds": 0.1,
    "tool_timeout_seconds": 0.05, "auto_connect": False,
}


class Bus:
    def __init__(self):
        self.events = []

    def emit(self, event_type, source, payload, **kwargs):
        name = event_type.value if hasattr(event_type, "value") else event_type
        self.events.append((name, source, payload))


class FakeClient:
    def __init__(self, server, *, unavailable=False, slow=False):
        self.server = server
        self.unavailable = unavailable
        self.slow = slow
        self.connected = False

    def connect(self, timeout):
        if self.unavailable:
            raise ConnectionError("offline")
        self.connected = True
        risk = RiskLevel.CRITICAL if self.server.metadata.get("destructive") else RiskLevel.CAUTION
        return (MCPTool(self.server.id, "echo", "Echo", {
            "type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"],
        }, risk),)

    def disconnect(self):
        self.connected = False
        return True

    def list_tools(self):
        return ()

    def call_tool(self, name, arguments, timeout):
        if self.slow:
            sleep(timeout + 0.01)
            raise TimeoutError
        return MCPToolResult(True, {"echo": arguments["text"]})

    def health_check(self):
        return self.connected


def server(**changes):
    values = dict(
        id="local", name="Local", transport=MCPTransport.STDIO,
        command_or_url="python", enabled=True, trusted=False, timeout=1, metadata={},
    )
    values.update(changes)
    return MCPServer(**values)


def manager(*, factory=FakeClient, policy=None, bus=None):
    event_bus = bus or Bus()
    security = policy or SecurityPolicyEngine({
        "confirmation_timeout_seconds": 30,
        "risk_policies": {"SAFE": "ALLOW", "CAUTION": "ALLOW", "CRITICAL": "CONFIRM"},
        "critical_confirmation_required": True, "logging_level": "INFO",
    }, event_bus=event_bus)
    registry = SkillRegistry(event_bus=event_bus, policy_engine=security)
    return MCPManager(CONFIG, skill_registry=registry, event_bus=event_bus, client_factory=factory), registry, event_bus


def test_server_registration_and_duplicate_rejection():
    mcp, _, _ = manager()
    item = mcp.register_server(server())
    assert mcp.list_servers() == [item]
    with pytest.raises(ValueError, match="already registered"):
        mcp.register_server(item)


def test_configuration_rejects_embedded_secrets_but_accepts_environment_names(tmp_path):
    target = tmp_path / "mcp.json"
    import json
    target.write_text(json.dumps({
        **CONFIG, "tool_timeout_seconds": 1,
        "servers": [{
            "id": "safe", "name": "Safe", "transport": "stdio", "command": "python",
            "metadata": {"environment_variables": {"SERVICE_TOKEN": "ISABELLA_SERVICE_TOKEN"}},
        }],
    }), encoding="utf-8")
    assert load_mcp_config(target)["servers"][0]["id"] == "safe"
    target.write_text(json.dumps({**CONFIG, "tool_timeout_seconds": 1, "api_token": "plain-secret"}), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="Secrets"):
        load_mcp_config(target)


def test_connect_discovers_separate_mcp_skill_and_disconnect_removes_it():
    mcp, registry, bus = manager()
    mcp.register_server(server())
    assert mcp.connect("local")
    assert [tool.skill_id for tool in mcp.list_tools()] == ["mcp.local.echo"]
    assert registry.get("mcp.local.echo").category == "mcp"
    result = mcp.call_tool("mcp.local.echo", {"text": "oi"})
    assert result.success and result.data["content"] == {"echo": "oi"}
    assert mcp.disconnect("local")
    assert registry.get("mcp.local.echo") is None
    names = [item[0] for item in bus.events]
    assert EventType.MCP_SERVER_CONNECTED.value in names
    assert EventType.MCP_TOOL_COMPLETED.value in names
    assert EventType.MCP_SERVER_DISCONNECTED.value in names


def test_server_unavailable_is_isolated_and_reported():
    mcp, _, _ = manager(factory=lambda item: FakeClient(item, unavailable=True))
    mcp.register_server(server())
    assert not mcp.connect("local")
    assert mcp.health_check()["recent_failures"] == 1
    assert mcp._states["local"] is MCPServerState.ERROR


def test_invalid_server_and_invalid_tool_are_rejected():
    mcp, _, _ = manager()
    with pytest.raises(KeyError):
        mcp.connect("missing")
    result = mcp.call_tool("mcp.local.missing", {})
    assert not result.success and result.error_code == "UNKNOWN_SKILL"


def test_tool_timeout_is_contained():
    mcp, _, bus = manager(factory=lambda item: FakeClient(item, slow=True))
    mcp.register_server(server())
    assert mcp.connect("local")
    result = mcp.call_tool("mcp.local.echo", {"text": "slow"})
    assert not result.success and result.error_code == "MCP_TOOL_ERROR"
    assert EventType.MCP_TOOL_FAILED.value in [item[0] for item in bus.events]
    mcp.shutdown()


def test_critical_external_metadata_needs_local_confirmation():
    mcp, _, _ = manager()
    mcp.register_server(server(metadata={"destructive": True}))
    assert mcp.connect("local")
    result = mcp.call_tool("mcp.local.echo", {"text": "delete"})
    assert result.status == "confirmation_required"


def test_security_denial_prevents_remote_call():
    policy = SecurityPolicyEngine({
        "confirmation_timeout_seconds": 30,
        "risk_policies": {"SAFE": "ALLOW", "CAUTION": "DENY", "CRITICAL": "CONFIRM"},
        "critical_confirmation_required": True, "logging_level": "INFO",
    })
    client = None

    def factory(item):
        nonlocal client
        client = FakeClient(item)
        return client

    mcp, _, _ = manager(factory=factory, policy=policy)
    mcp.register_server(server())
    assert mcp.connect("local")
    result = mcp.call_tool("mcp.local.echo", {"text": "blocked"})
    assert result.status == "denied"
    assert client.connected
    mcp.shutdown()


def test_shutdown_disconnects_every_server_and_disabled_has_negligible_work():
    mcp, registry, _ = manager()
    mcp.register_server(server())
    assert mcp.connect("local")
    assert mcp.shutdown()
    assert not mcp._clients and registry.get("mcp.local.echo") is None
    disabled = MCPManager({**CONFIG, "enabled": False}, skill_registry=registry)
    assert disabled.start() and disabled.health_check()["connected_servers"] == 0


def test_official_sdk_stdio_local_server_round_trip():
    mcp, _, _ = manager(factory=MCPClient)
    mcp.connection_timeout = 10
    fixture = Path(__file__).parent / "fixtures" / "mcp_stdio_server.py"
    mcp.register_server(server(
        command_or_url=sys.executable, timeout=10,
        metadata={"args": [str(fixture)]},
    ))
    assert mcp.connect("local")
    result = mcp.call_tool("mcp.local.echo", {"text": "isabella"})
    assert result.success
    assert result.data["content"] == {"echo": "isabella"}
    assert mcp.shutdown()
