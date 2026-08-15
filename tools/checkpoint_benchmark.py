"""Reproducible controlled benchmark for the Phase 15 stability checkpoint."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
from time import perf_counter, sleep

import psutil

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Isabella.Context.manager import ContextManager
from Isabella.Context.providers import ActiveWindow
from Isabella.Events import EventBus
from Isabella.Intelligence.brain import Brain
from Isabella.Intelligence.planner import Planner
from Isabella.Intelligence.router import Router
from Isabella.Memory.database import MemoryDatabase
from Isabella.Memory.manager import MemoryManager
from Isabella.Security import SecurityPolicyEngine
from Isabella.Skills.base import ParameterSpec, RiskLevel, SkillDefinition, SkillResult
from Isabella.Skills.registry import SkillRegistry


class FakeLLM:
    def __init__(self) -> None:
        self.latencies_ms = []

    def chat(self, message: str) -> str:
        started = perf_counter()
        response = "Resposta controlada."
        self.latencies_ms.append((perf_counter() - started) * 1000)
        return response

    def close(self) -> None:
        return None


class FakeProvider:
    def active_window(self) -> ActiveWindow:
        return ActiveWindow("code", "ISABELLA", True)

    def connected_devices(self) -> dict[str, str]:
        return {"microphone": "simulated"}


def memory_config(path: Path) -> dict:
    return {
        "enabled": True, "database_path": str(path),
        "working_memory_max_messages": 30, "max_retrieval_results": 5,
    }


def context_config() -> dict:
    return {
        "enabled": True, "active_window_lookup": True,
        "refresh_interval_seconds": 1, "reference_confidence_threshold": 0.7,
    }


def event_config() -> dict:
    return {
        "enabled": True, "queue_max_size": 1000, "worker_count": 2,
        "high_priority_reserve": 50, "shutdown_timeout_seconds": 3,
    }


def security_config() -> dict:
    return {
        "confirmation_timeout_seconds": 30,
        "risk_policies": {"SAFE": "ALLOW", "CAUTION": "ALLOW", "CRITICAL": "CONFIRM"},
        "critical_confirmation_required": True, "logging_level": "WARNING",
    }


def definition(skill_id: str, risk: RiskLevel = RiskLevel.SAFE) -> SkillDefinition:
    parameters = {"name": ParameterSpec(str)} if skill_id == "applications.open" else {}
    if skill_id == "browser.open_url":
        parameters = {"target": ParameterSpec(str, required=False)}

    def execute(arguments):
        if arguments.get("name") == "missing":
            return SkillResult(False, skill_id, "Falha simulada.", status="failed")
        return SkillResult(True, skill_id, "Ação controlada concluída.")

    return SkillDefinition(skill_id, skill_id, "benchmark", "benchmark", parameters, risk, execute)


def snapshot(process: psutil.Process) -> dict:
    return {
        "ram_mb": process.memory_info().rss / (1024 * 1024),
        "threads": threading.active_count(),
    }


def main() -> None:
    process = psutil.Process()
    process.cpu_percent(interval=None)
    sleep(0.2)
    cpu_idle = process.cpu_percent(interval=None)
    with tempfile.TemporaryDirectory(prefix="isabella_checkpoint_") as directory:
        temporary = Path(directory)
        bus = EventBus(event_config())
        security = SecurityPolicyEngine(security_config(), event_bus=bus)
        registry = SkillRegistry(event_bus=bus, policy_engine=security)
        for item in (
            definition("applications.open"), definition("browser.open_url"),
            definition("vision.capture_screen"), definition("system.shutdown", RiskLevel.CRITICAL),
        ):
            registry.register(item)
        memory = MemoryManager(memory_config(temporary / "memory.db"), MemoryDatabase(temporary / "memory.db"), bus)
        context = ContextManager(context_config(), provider=FakeProvider(), memory=memory, event_bus=bus)
        router = Router()
        planner = Planner(router=router, event_bus=bus)
        brain = Brain(
            FakeLLM(), router=router, planner=planner, registry=registry,
            memory=memory, context=context, event_bus=bus, security=security,
        )
        commands = (
            ("Explique o checkpoint.", "text"),
            ("Abra o Chrome.", "voice"),
            ("Abra o Chrome e depois abra o YouTube.", "text"),
            ("Lembre que o projeto atual se chama ISABELLA.", "text"),
            ("Faça de novo.", "voice"),
            ("Tire uma captura da tela.", "text"),
            ("Abra missing.", "text"),
            ("Desligue o computador.", "voice"),
        )
        measurements = {"0": snapshot(process)}
        started = perf_counter()
        for index in range(1, 101):
            text, source = commands[(index - 1) % len(commands)]
            brain.process(text, request_id=f"checkpoint-{index}", input_source=source)
            if index % 10 == 0:
                memory.search("projeto")
                context.resolve_reference("Feche ele")
            if index in {25, 50, 100}:
                bus.wait_until_idle()
                measurements[str(index)] = snapshot(process)
        total_ms = (perf_counter() - started) * 1000
        bus.wait_until_idle()
        diagnostics = {
            "interactions": 100,
            "total_ms": total_ms,
            "average_interaction_ms": total_ms / 100,
            "cpu_idle_percent": cpu_idle,
            "measurements": measurements,
            "router_ms": router.average_latency_ms,
            "planner_ms": planner.average_latency_ms,
            "skill_validation_ms": registry.average_validation_latency_ms,
            "skill_execution_ms": registry.average_execution_latency_ms,
            "memory_retrieval_ms": sum(memory.metrics["retrieval_ms"]) / len(memory.metrics["retrieval_ms"]) if memory.metrics["retrieval_ms"] else 0.0,
            "context_resolution_ms": sum(context.metrics["reference_resolution_ms"]) / len(context.metrics["reference_resolution_ms"]) if context.metrics["reference_resolution_ms"] else 0.0,
            "event_bus": bus.diagnostics(),
            "working_memory_messages": len(memory.working_memory),
        }
        brain.shutdown()
        bus.shutdown()
        diagnostics["threads_after_shutdown"] = threading.active_count()
        diagnostics["temporary_files_after_shutdown"] = len(list(temporary.rglob("*")))
        print(json.dumps(diagnostics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
