from pathlib import Path

from Isabella.Context.manager import ContextManager
from Isabella.Context.models import ContextSnapshot
from Isabella.Context.providers import ActiveWindow
from Isabella.Intelligence.brain import Brain
from Isabella.Memory.manager import MemoryManager
from Isabella.Memory.models import MemoryType
from Isabella.Skills.base import ParameterSpec, RiskLevel, SkillDefinition, SkillResult
from Isabella.Skills.registry import SkillRegistry


CONTEXT_CONFIG = {
    "enabled": True,
    "active_window_lookup": True,
    "refresh_interval_seconds": 1.0,
    "reference_confidence_threshold": 0.8,
}


class FakeProvider:
    def __init__(self, application="chrome", title="Chrome"):
        self.application = application
        self.title = title
        self.calls = 0

    def active_window(self):
        self.calls += 1
        return ActiveWindow(self.application, self.title, self.application != "unavailable")

    def connected_devices(self):
        return {"microphone": "Test Mic", "audio_output": "Test Speaker"}


class FailingProvider(FakeProvider):
    def active_window(self):
        raise OSError("provider failed")


class FakeLLM:
    def chat(self, text):
        return "Resposta contextual."

    def close(self):
        return None


def memory_config(path: Path):
    return {
        "enabled": True, "database_path": str(path),
        "working_memory_max_messages": 30, "max_retrieval_results": 5,
    }


def make_registry(calls):
    registry = SkillRegistry()

    def executor(skill):
        return lambda args: (calls.append((skill, dict(args))) or SkillResult(True, skill, "concluído", dict(args)))

    registry.register(SkillDefinition(
        "applications.open", "Open", "Open app", "applications",
        {"name": ParameterSpec(str)}, RiskLevel.SAFE, executor("applications.open"),
    ))
    registry.register(SkillDefinition(
        "applications.close", "Close", "Close app", "applications",
        {"name": ParameterSpec(str)}, RiskLevel.CAUTION, executor("applications.close"),
    ))
    registry.register(SkillDefinition(
        "system.shutdown", "Shutdown", "Shutdown", "system", {}, RiskLevel.CRITICAL,
        executor("system.shutdown"),
    ))
    return registry


def test_context_snapshot_has_safe_defaults():
    context = ContextManager(CONTEXT_CONFIG, provider=FakeProvider())
    snapshot = context.get_snapshot()
    assert isinstance(snapshot, ContextSnapshot)
    assert snapshot.session_id
    assert snapshot.active_application == "unavailable"
    assert snapshot.connected_devices == {}


def test_update_get_set_clear_and_reset_session():
    context = ContextManager(CONTEXT_CONFIG, provider=FakeProvider())
    original_session = context.get("session_id")
    context.update(active_application="chrome", current_project="ISABELLA")
    assert context.get("active_application") == "chrome"
    context.clear("active_application")
    assert context.get("active_application") == "unavailable"
    context.reset_session()
    assert context.get("session_id") != original_session
    assert context.get("current_project") is None


def test_last_action_and_result_are_structured():
    context = ContextManager(CONTEXT_CONFIG, provider=FakeProvider())
    context.record_action("applications.open", {"name": "chrome"}, "SAFE")
    context.record_result(True, "Chrome aberto", {"application": "chrome", "traceback": "hidden"})
    snapshot = context.get_snapshot()
    assert snapshot.last_skill == "applications.open"
    assert snapshot.last_action.arguments == {"name": "chrome"}
    assert snapshot.last_result.success
    assert "traceback" not in snapshot.last_result.data


def test_reference_resolves_from_last_action_with_high_confidence():
    context = ContextManager(CONTEXT_CONFIG, provider=FakeProvider("python", "I.S.A.B.E.L.L.A."))
    context.record_action("applications.open", {"name": "chrome"})
    context.refresh_active_window(force=True)
    resolved = context.resolve_reference("Feche ele")
    assert resolved.entity == "chrome"
    assert resolved.confidence == 0.95


def test_ambiguous_reference_is_not_resolved():
    context = ContextManager(CONTEXT_CONFIG, provider=FakeProvider("discord", "Discord"))
    context.record_action("applications.open", {"name": "chrome"})
    context.refresh_active_window(force=True)
    resolved = context.resolve_reference("Feche ele")
    assert resolved.entity is None
    assert resolved.confidence < context.confidence_threshold


def test_explicit_active_program_reference_prefers_real_window():
    context = ContextManager(CONTEXT_CONFIG, provider=FakeProvider("discord", "Discord"))
    context.record_action("applications.open", {"name": "chrome"})
    context.refresh_active_window(force=True)
    resolved = context.resolve_reference("Feche o programa que está aberto")
    assert resolved.entity == "discord"
    assert resolved.source == "active_application"


def test_provider_failure_marks_window_unavailable_without_crashing():
    context = ContextManager(CONTEXT_CONFIG, provider=FailingProvider())
    snapshot = context.refresh_active_window(force=True)
    assert snapshot.active_application == "unavailable"
    assert context.status == "DEGRADED"


def test_window_lookup_respects_refresh_interval():
    provider = FakeProvider()
    context = ContextManager(CONTEXT_CONFIG, provider=provider)
    context.refresh_active_window()
    context.refresh_active_window()
    assert provider.calls == 1


def test_memory_and_context_resolve_preferred_browser(tmp_path):
    memory = MemoryManager(memory_config(tmp_path / "memory.db"))
    memory.remember(MemoryType.PREFERENCE, "preferred_browser", "chrome")
    context = ContextManager(CONTEXT_CONFIG, provider=FakeProvider(), memory=memory)
    resolved = context.resolve_reference("Abra o navegador")
    assert resolved.entity == "chrome"
    assert resolved.source == "memory_preference"
    memory.close()


def test_project_memory_initializes_and_survives_context_reset(tmp_path):
    memory = MemoryManager(memory_config(tmp_path / "memory.db"))
    memory.remember(MemoryType.PROJECT, "current_project_name", "ISABELLA")
    context = ContextManager(CONTEXT_CONFIG, provider=FakeProvider(), memory=memory)
    assert context.get("current_project") == "ISABELLA"
    context.reset_session()
    assert context.get("current_project") == "ISABELLA"
    memory.close()


def test_brain_resolves_pronoun_before_closing_application():
    calls = []
    context = ContextManager(CONTEXT_CONFIG, provider=FakeProvider("python", "I.S.A.B.E.L.L.A."))
    brain = Brain(FakeLLM(), registry=make_registry(calls), context=context)
    brain.process("Abra o Chrome")
    response = brain.process("Feche ele")
    assert response.message == "concluído"
    assert calls[-1] == ("applications.close", {"name": "chrome"})
    brain.shutdown()


def test_do_again_repeats_safe_action():
    calls = []
    context = ContextManager(CONTEXT_CONFIG, provider=FakeProvider("python", "I.S.A.B.E.L.L.A."))
    brain = Brain(FakeLLM(), registry=make_registry(calls), context=context)
    brain.process("Abra o Chrome")
    brain.process("Faça de novo")
    assert calls == [
        ("applications.open", {"name": "chrome"}),
        ("applications.open", {"name": "chrome"}),
    ]
    brain.shutdown()


def test_critical_repeat_requires_fresh_confirmation():
    calls = []
    context = ContextManager(CONTEXT_CONFIG, provider=FakeProvider("python", "I.S.A.B.E.L.L.A."))
    brain = Brain(FakeLLM(), registry=make_registry(calls), context=context)
    first = brain.process("Desligue o computador")
    second = brain.process("Faça de novo")
    assert first.skill_results[0].status == "confirmation_required"
    assert second.skill_results[0].status == "confirmation_required"
    assert calls == []
    brain.shutdown()


def test_continue_without_resumable_plan_is_safe():
    context = ContextManager(CONTEXT_CONFIG, provider=FakeProvider())
    response = Brain(FakeLLM(), context=context).process("continue")
    assert "não há uma tarefa pendente" in response.message.lower()


def test_context_metrics_are_recorded():
    context = ContextManager(CONTEXT_CONFIG, provider=FakeProvider())
    context.get_snapshot()
    context.refresh_active_window(force=True)
    context.resolve_reference("Feche ele")
    assert all(context.metrics[name] for name in context.metrics)
