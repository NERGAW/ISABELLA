from collections import deque
from pathlib import Path
from types import SimpleNamespace

from Isabella.Diagnostics import DiagnosticsManager, HealthStatus, Subsystem
from Isabella.Intelligence.router import Router
from Isabella.Skills import create_diagnostics_skill
from Isabella.Skills.base import RiskLevel
from Isabella.Skills.registry import SkillRegistry


CONFIG = {
    "enabled": True,
    "check_interval_seconds": 15,
    "expensive_check_interval_seconds": 60,
    "failure_history_limit": 3,
}


class FakeLLM:
    model = "qwen3:1.7b"
    average_latency_ms = 12.5
    recent_errors = deque(maxlen=5)

    def __init__(self, reachable=True, model_available=True):
        self.reachable = reachable
        self.model_available = model_available

    def health_check(self):
        return self.reachable

    def list_models(self):
        return [self.model] if self.model_available else ["another:latest"]


class FakeDatabase:
    path = Path("missing-diagnostics-test.db")

    def __init__(self, healthy=True):
        self.healthy = healthy

    def health_check(self):
        return self.healthy


class FakeVision:
    status = "ONLINE"

    def __init__(self, screen=True, camera=True):
        self.screen = screen
        self.camera = camera

    def health_check(self, check_camera=False):
        return {"screen": self.screen, "camera": self.camera if check_camera else None}


class FakeSecurity:
    pending_count = 2
    expired_count = 1

    def expire_pending(self):
        return 0


class FakeMCP:
    def health_check(self):
        return {
            "enabled": True, "registered_servers": 0, "connected_servers": 0,
            "available_tools": 0, "recent_failures": 0, "unhealthy_servers": [],
        }


class FakeBus:
    def __init__(self, failed=0, dropped=0):
        self.failed = failed
        self.dropped = dropped
        self.events = []

    def diagnostics(self):
        return {
            "queue_size": 0, "processed_count": 10, "failed_count": self.failed,
            "dropped_count": self.dropped, "subscriber_count": 2,
        }

    def emit(self, event_type, source, payload):
        self.events.append((event_type.value, source, payload))


def components(**changes):
    listener = SimpleNamespace(
        state=SimpleNamespace(value="LISTENING"), stt=SimpleNamespace(_model=object()),
        _audio_queue=SimpleNamespace(qsize=lambda: 0),
    )
    tts = SimpleNamespace(
        state="READY", health_check=lambda: True, _active_provider=SimpleNamespace(name="edge"),
        _queue=SimpleNamespace(qsize=lambda: 0),
    )
    bus = changes.get("event_bus", FakeBus())
    app = SimpleNamespace(
        status=SimpleNamespace(value="ONLINE"), voice_listener=changes.get("listener", listener),
        tts_manager=changes.get("tts", tts), event_bus=bus,
    )
    memory = changes.get("memory", SimpleNamespace(
        status="ONLINE", database=FakeDatabase(), last_write_at="write", last_read_at="read",
    ))
    brain = SimpleNamespace(
        llm=changes.get("llm", FakeLLM()), router=SimpleNamespace(average_latency_ms=0.1),
        planner=SimpleNamespace(average_latency_ms=0.2),
        registry=SimpleNamespace(list=lambda: [1, 2]), memory=memory,
        context=SimpleNamespace(status="ONLINE"), vision=changes.get("vision", FakeVision()),
        security=changes.get("security", FakeSecurity()),
        mcp=changes.get("mcp", FakeMCP()),
    )
    controller = SimpleNamespace(
        state=SimpleNamespace(value="IDLE"),
        thread_pool=SimpleNamespace(activeThreadCount=lambda: 0),
    )
    return app, brain, controller, bus


def manager(**changes):
    app, brain, controller, bus = components(**changes)
    return DiagnosticsManager(CONFIG, app=app, brain=brain, controller=controller, event_bus=bus)


def test_all_subsystems_and_metrics_are_reported():
    report = manager().check(detailed=True, expensive=True)
    assert set(report.statuses) == {item.value for item in Subsystem}
    assert report.statuses["LLM"].details["model_available"] is True
    assert report.statuses["MEMORY"].details["last_write"] == "write"
    assert report.statuses["SECURITY"].details == {
        "policy_loaded": True, "pending_confirmations": 2, "expired_confirmations": 1,
    }
    assert report.statuses["MCP"].status is HealthStatus.ONLINE
    assert report.metrics.process_memory_mb > 0
    assert report.metrics.thread_count > 0
    assert report.summary == "Todos os sistemas principais estão operacionais."


def test_ollama_offline_is_reported():
    report = manager(llm=FakeLLM(reachable=False)).check(expensive=True)
    assert report.statuses["LLM"].status is HealthStatus.OFFLINE


def test_missing_model_degrades_llm():
    report = manager(llm=FakeLLM(model_available=False)).check(expensive=True)
    assert report.statuses["LLM"].status is HealthStatus.DEGRADED


def test_microphone_error_is_reported():
    listener = SimpleNamespace(
        state=SimpleNamespace(value="ERROR"), stt=SimpleNamespace(_model=None),
        _audio_queue=SimpleNamespace(qsize=lambda: 0),
    )
    report = manager(listener=listener).check()
    assert report.statuses["VOICE INPUT"].status is HealthStatus.ERROR


def test_tts_offline_is_reported():
    tts = SimpleNamespace(
        state="ERROR", health_check=lambda: False, _active_provider=None,
        _queue=SimpleNamespace(qsize=lambda: 3),
    )
    report = manager(tts=tts).check()
    assert report.statuses["VOICE OUTPUT"].status is HealthStatus.ERROR
    assert report.statuses["VOICE OUTPUT"].details["queue_size"] == 3


def test_memory_error_is_reported():
    memory = SimpleNamespace(
        status="ERROR", database=FakeDatabase(False), last_write_at=None, last_read_at=None,
    )
    report = manager(memory=memory).check()
    assert report.statuses["MEMORY"].status is HealthStatus.ERROR


def test_vision_error_and_camera_degraded_are_reported():
    assert manager(vision=FakeVision(screen=False)).check(expensive=True).statuses["VISION"].status is HealthStatus.ERROR
    assert manager(vision=FakeVision(screen=True, camera=False)).check(expensive=True).statuses["VISION"].status is HealthStatus.DEGRADED


def test_event_bus_failures_degrade_status():
    report = manager(event_bus=FakeBus(failed=1, dropped=2)).check()
    assert report.statuses["EVENT BUS"].status is HealthStatus.DEGRADED


def test_status_event_only_emits_on_change_and_history_is_bounded():
    bus = FakeBus()
    diagnostics = manager(event_bus=bus)
    diagnostics.check()
    diagnostics.check()
    assert bus.events == []
    bus.failed = 1
    diagnostics.check()
    assert [event[0] for event in bus.events] == ["diagnostics.status_changed"]
    for _ in range(5):
        bus.failed = 0 if bus.failed else 1
        diagnostics.check()
    assert len(diagnostics.failure_history) <= 3


def test_manager_does_not_start_runtime_automatically_and_overhead_is_small():
    diagnostics = manager()
    for _ in range(20):
        diagnostics.check()
    assert diagnostics._thread is None
    assert diagnostics.average_check_ms < 50


def test_diagnostics_command_routes_to_safe_skill():
    router = Router()
    request = router.skill_request("Isabella, diagnóstico detalhado.")
    assert request.skill == "system.diagnostics"
    assert request.arguments == {"detailed": True}


def test_diagnostics_skill_is_safe_and_returns_summary():
    diagnostics = manager()
    definition = create_diagnostics_skill(diagnostics)
    registry = SkillRegistry()
    registry.register(definition)
    result = registry.execute("system.diagnostics", {"detailed": False})
    assert definition.risk_level is RiskLevel.SAFE
    assert result.success
    assert result.data["report"]["summary"] == result.message
