from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from Isabella.ControlCenter.controller import ControlCenterController
from Isabella.ControlCenter.window import ControlCenterWindow
from Isabella.Security import PolicyDecision, PolicyResult


class DummyBus:
    def subscribe(self, *_): pass
    def unsubscribe(self, *_): pass


class DummyDiagnostics:
    def check(self, detailed=False):
        return {"statuses": {"CORE": {"status": "ONLINE"}}, "metrics": {"cpu_percent": 1.0}}


class DummyMemory:
    def list_memories(self): return []
    def search(self, query, limit=50): return []
    def forget(self, key, memory_type=None): return 1


class DummyManager:
    def list(self): return []
    def enable(self, value): return value
    def disable(self, value): return value
    def cancel(self, value): return value


class DummyRegistry:
    def list(self): return []


class DummySecurity:
    pending_count = 0
    expired_count = 0
    def evaluate(self, *args): return PolicyResult(PolicyDecision.ALLOW, "test")


@pytest.fixture
def runtime():
    brain = SimpleNamespace(
        diagnostics=DummyDiagnostics(), registry=DummyRegistry(), memory=DummyMemory(),
        automations=DummyManager(), scheduler=DummyManager(),
        security=DummySecurity(),
        llm=SimpleNamespace(model="test"), latencies_ms=[], router=object(), planner=object(),
    )
    return SimpleNamespace(
        brain=brain, event_bus=DummyBus(), nodes=None, home=None, device_security=None,
        report=lambda: {"runtime": "ONLINE", "startup_ms": 10, "services": {"Core": {"state": "ONLINE"}}},
        restart_service=lambda name: name == "API",
    )


def test_open_close_and_status(runtime):
    app = QApplication.instance() or QApplication([])
    controller = ControlCenterController(runtime)
    window = ControlCenterWindow(controller)
    assert controller.refresh().overview["CORE"] == "ONLINE"
    window.show(); app.processEvents(); window.close()


def test_admin_actions_and_core_restart_denial(runtime):
    controller = ControlCenterController(runtime)
    with pytest.raises(PermissionError): controller.delete_memory("x")
    controller.set_administrative(True)
    assert controller.delete_memory("x") == 1
    assert controller.set_automation_enabled("a", False) == "a"
    assert controller.cancel_task("t") == "t"
    assert controller.restart_service("API") is True
    with pytest.raises(PermissionError): controller.restart_service("Core")


def test_log_tail_filters(runtime, monkeypatch, tmp_path):
    controller = ControlCenterController(runtime)
    from Isabella.ControlCenter import controller as module
    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    (tmp_path / "logs").mkdir(); (tmp_path / "logs" / "isabella.log").write_text("INFO CORE ok\nERROR API bad\n", encoding="utf-8")
    assert controller.read_logs(module="API", level="ERROR") == ["ERROR API bad"]
