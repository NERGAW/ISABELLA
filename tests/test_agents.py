from time import perf_counter

import pytest

from Isabella.Agents import AgentOrchestrator
from Isabella.Events import EventType
from Isabella.Intelligence.models import Intent


class Bus:
    def __init__(self): self.events = []
    def emit(self, event_type, source, payload): self.events.append((getattr(event_type, "value", event_type), payload))


@pytest.fixture
def orchestrator(): return AgentOrchestrator(event_bus=Bus(), max_agent_hops=3)


@pytest.mark.parametrize(("text", "expected"), [
    ("Abra o Chrome", "SYSTEM_AGENT"),
    ("Pesquise fontes atuais", "RESEARCH_AGENT"),
    ("O que aparece na tela?", "VISION_AGENT"),
    ("Qual é minha preferência?", "MEMORY_AGENT"),
    ("Analise este traceback no VS Code", "ENGINEERING_AGENT"),
])
def test_agent_selection(orchestrator, text, expected):
    assert expected in orchestrator.select(text)


def test_multi_agent_sequential_delegation(orchestrator):
    selected = orchestrator.select("Veja o erro na tela e pesquise uma solução")
    assert selected == ("VISION_AGENT", "RESEARCH_AGENT")
    outputs, failure = orchestrator.execute(selected, "task", lambda agent, task: f"{task.hop}:{agent.id}")
    assert failure is None and outputs == ["1:VISION_AGENT", "2:RESEARCH_AGENT"]
    names = [event[0] for event in orchestrator.event_bus.events]
    assert EventType.AGENT_DELEGATED.value in names


def test_loop_protection_and_failure(orchestrator):
    with pytest.raises(RuntimeError):
        orchestrator.execute(("SYSTEM_AGENT",) * 4, "loop", lambda *_: None)
    outputs, failure = orchestrator.execute(("VISION_AGENT",), "fail", lambda *_: (_ for _ in ()).throw(RuntimeError()))
    assert outputs == [] and not failure.success
    assert orchestrator.diagnostics()["failures"]["VISION_AGENT"] == 1


def test_shared_context_is_minimized(orchestrator):
    observed = {}
    orchestrator.execute(("MEMORY_AGENT",), "recall", lambda _agent, task: observed.update(task.context), context={"current_project": "ISABELLA", "password": "never-share"})
    assert observed == {"current_project": "ISABELLA"}


def test_simple_selection_overhead_is_negligible(orchestrator):
    started = perf_counter()
    for _ in range(1000): assert orchestrator.select("Olá, tudo bem?", intent=Intent.CONVERSATION) == ()
    assert (perf_counter() - started) * 1000 < 100
