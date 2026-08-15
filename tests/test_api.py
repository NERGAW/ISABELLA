import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from Isabella.API import LocalAPIServer
from Isabella.API.server import load_api_config
from Isabella.Diagnostics.models import DiagnosticsReport, SystemMetrics
from Isabella.Events import EventType
from Isabella.Intelligence.brain import Brain
from Isabella.Security import SecurityPolicyEngine
from Isabella.Skills.base import RiskLevel, SkillDefinition, SkillResult
from Isabella.Skills.registry import SkillRegistry


class Bus:
    def __init__(self):
        self.events = []

    def emit(self, event_type, source, payload=None, **kwargs):
        name = event_type.value if hasattr(event_type, "value") else event_type
        self.events.append((name, kwargs.get("correlation_id")))
        return True


class LLM:
    def chat(self, text):
        return "conversa"


class Diagnostics:
    def check(self, **kwargs):
        return DiagnosticsReport({}, SystemMetrics(0, 0, 1, 1, {}, 1), "Tudo operacional.", True)


class Runtime:
    def report(self):
        return {"runtime": "ONLINE", "services": {}}


def brain():
    policy = SecurityPolicyEngine({"confirmation_timeout_seconds": 30, "risk_policies": {"SAFE": "ALLOW", "CAUTION": "ALLOW", "CRITICAL": "CONFIRM"}, "critical_confirmation_required": True, "logging_level": "INFO"})
    registry = SkillRegistry(policy_engine=policy)
    registry.register(SkillDefinition("applications.open", "Open", "test", "applications", {"name": SimpleNamespace(value_type=str, required=True)}, RiskLevel.SAFE, lambda args: SkillResult(True, "applications.open", "Aberto.")))
    registry.register(SkillDefinition("system.shutdown", "Shutdown", "test", "system", {}, RiskLevel.CRITICAL, lambda args: SkillResult(True, "system.shutdown", "Nunca deve executar.")))
    result = Brain(LLM(), registry=registry, security=policy)
    result.diagnostics = Diagnostics()
    return result


def config(tmp_path, **changes):
    result = {"enabled": True, "host": "127.0.0.1", "port": 0, "allow_remote": False, "authentication_required": True, "token_file": str(tmp_path / "token.txt"), "cors_allowed_origins": [], "command_rate_limit": 2, "rate_window_seconds": 60, "max_request_bytes": 4096}
    result.update(changes)
    return result


def request(server, path, method="GET", payload=None, token=None, request_id=None):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if request_id:
        headers["X-Request-ID"] = request_id
    data = None if payload is None else json.dumps(payload).encode()
    req = Request(f"http://127.0.0.1:{server.port}{path}", data=data, method=method, headers=headers)
    try:
        response = urlopen(req, timeout=2)
    except HTTPError as exc:
        response = exc
    return response.status, json.loads(response.read().decode()), dict(response.headers)


def server(tmp_path, **changes):
    bus = Bus()
    api = LocalAPIServer(config(tmp_path, **changes), brain=brain(), runtime=Runtime(), event_bus=bus)
    assert api.start()
    return api, bus


def test_health_is_local_public_and_structured(tmp_path):
    api, _ = server(tmp_path)
    code, body, headers = request(api, "/health", request_id="health-id")
    assert code == 200 and body["success"] and body["request_id"] == "health-id"
    assert body["data"]["local"] is True
    assert headers["X-Request-ID"] == "health-id"
    api.shutdown()


def test_status_skills_and_diagnostics_require_valid_token(tmp_path):
    api, _ = server(tmp_path)
    token = api.authentication.token_for_local_setup
    assert request(api, "/status")[0] == 401
    assert request(api, "/skills", token="invalid")[0] == 401
    assert request(api, "/status", token=token)[1]["data"]["runtime"] == "ONLINE"
    assert request(api, "/skills", token=token)[1]["data"]["skills"][0]["risk_level"] in {"SAFE", "CRITICAL"}
    assert request(api, "/diagnostics", token=token)[1]["message"] == "Tudo operacional."
    api.shutdown()


def test_authenticated_command_and_correlation_events(tmp_path):
    api, bus = server(tmp_path)
    code, body, _ = request(api, "/command", "POST", {"text": "abra o Chrome"}, api.authentication.token_for_local_setup, "api-command-1")
    assert code == 200 and body["success"] and body["status"] == "completed"
    assert (EventType.API_REQUEST_RECEIVED.value, "api-command-1") in bus.events
    assert (EventType.API_REQUEST_COMPLETED.value, "api-command-1") in bus.events
    api.shutdown()


def test_critical_command_cannot_be_silently_confirmed(tmp_path):
    api, _ = server(tmp_path)
    code, body, _ = request(api, "/command", "POST", {"text": "desligue o computador"}, api.authentication.token_for_local_setup)
    assert code == 200 and not body["success"]
    assert body["status"] == "confirmation_required"
    assert body["error"] == "CONFIRMATION_REQUIRED"
    api.shutdown()


def test_invalid_payload_and_rate_limit(tmp_path):
    api, bus = server(tmp_path)
    token = api.authentication.token_for_local_setup
    assert request(api, "/command", "POST", {"wrong": "field"}, token)[0] == 400
    assert request(api, "/command", "POST", {"text": "oi"}, token)[0] == 200
    assert request(api, "/command", "POST", {"text": "oi de novo"}, token)[0] == 429
    assert EventType.API_RATE_LIMITED.value in {item[0] for item in bus.events}
    api.shutdown()


def test_malformed_json_still_gets_request_id_and_metrics(tmp_path):
    api, bus = server(tmp_path)
    req = Request(f"http://127.0.0.1:{api.port}/command", data=b"{", method="POST", headers={"Authorization": f"Bearer {api.authentication.token_for_local_setup}", "Content-Type": "application/json"})
    with pytest.raises(HTTPError) as captured:
        urlopen(req, timeout=2)
    body = json.loads(captured.value.read().decode())
    assert captured.value.code == 400 and len(body["request_id"]) == 32
    assert body["error"] == "INVALID_JSON"
    assert api.health_check()["errors"] == 1
    assert (EventType.API_REQUEST_RECEIVED.value, body["request_id"]) in bus.events
    api.shutdown()


def test_auth_failure_metrics_and_token_is_not_in_config(tmp_path):
    api, bus = server(tmp_path)
    assert "token" not in api.config
    request(api, "/command", "POST", {"text": "oi"})
    details = api.health_check()
    assert details["auth_failures"] == 1
    assert EventType.API_AUTH_FAILED.value in {item[0] for item in bus.events}
    assert Path(api.config["token_file"]).read_text().strip() == api.authentication.token_for_local_setup
    api.shutdown()


def test_api_disabled_does_not_bind_or_generate_token(tmp_path):
    token = tmp_path / "token.txt"
    api = LocalAPIServer(config(tmp_path, enabled=False), brain=brain())
    assert api.start() and api.status == "DISABLED" and api._server is None
    assert not token.exists()


def test_configuration_forbids_remote_default_and_wildcard_cors(tmp_path):
    path = tmp_path / "api.json"
    value = config(tmp_path, host="0.0.0.0")
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(Exception, match="allow_remote"):
        load_api_config(path)
    value.update({"host": "127.0.0.1", "cors_allowed_origins": ["*"]})
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(Exception, match="CORS"):
        load_api_config(path)
