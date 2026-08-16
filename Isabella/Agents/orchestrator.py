from __future__ import annotations

from collections import Counter, deque
from time import perf_counter
import re

from Isabella.Events import EventType
from .base import AgentTask
from .registry import AgentRegistry
from .agents import build_specialized_agents


class AgentOrchestrator:
    def __init__(self, *, event_bus=None, max_agent_hops: int = 3):
        if not 1 <= int(max_agent_hops) <= 5: raise ValueError("max_agent_hops must be between 1 and 5")
        self.event_bus = event_bus; self.max_agent_hops = int(max_agent_hops)
        self.registry = AgentRegistry()
        for agent in build_specialized_agents(): self.registry.register(agent)
        self.usage = Counter(); self.failures = Counter(); self.latencies_ms: deque[float] = deque(maxlen=200)
        self.recent_activity: deque[dict] = deque(maxlen=100)

    def select(self, text: str, *, intent=None, mode: str = "NORMAL") -> tuple[str, ...]:
        normalized = text.casefold()
        visual = any(word in normalized for word in ("tela", "imagem", "camera", "erro visível", "erro na tela"))
        research = any(word in normalized for word in ("pesquis", "busque", "fontes", "notícias", "solução"))
        memory = any(word in normalized for word in ("lembre", "memória", "recorda", "preferência", "projeto atual"))
        engineering = mode == "ENGINEERING" or any(word in normalized for word in ("código", "git", "github", "vscode", "vs code", "log", "traceback"))
        selected = []
        if visual: selected.append("VISION_AGENT")
        if research: selected.append("RESEARCH_AGENT")
        if memory and not selected: selected.append("MEMORY_AGENT")
        if engineering and not selected: selected.append("ENGINEERING_AGENT")
        if not selected and self._system_request(normalized, intent): selected.append("SYSTEM_AGENT")
        return tuple(selected[:self.max_agent_hops])

    @staticmethod
    def _system_request(text, intent):
        return any(word in text for word in ("abra", "feche", "volume", "diagnóst", "deslig", "reinici")) or getattr(intent, "value", intent) in {"single_skill", "multi_step"}

    def execute(self, agent_ids, text: str, handler, *, context: dict | None = None):
        ids = tuple(agent_ids)
        if len(ids) > self.max_agent_hops: raise RuntimeError("AGENT_HOP_LIMIT")
        outputs = []
        for hop, agent_id in enumerate(ids, 1):
            agent = self.registry.get(agent_id)
            if not agent: raise ValueError(f"Unknown agent: {agent_id}")
            if hop > 1: self._emit(EventType.AGENT_DELEGATED, {"from": ids[hop - 2], "to": agent_id, "hop": hop})
            safe_context = {key: (context or {}).get(key) for key in agent.required_context if key in (context or {})}
            task = AgentTask(text, safe_context, hop)
            started = perf_counter(); self._emit(EventType.AGENT_STARTED, {"agent_id": agent_id, "hop": hop})
            result = agent.execute(task, handler)
            latency = (perf_counter() - started) * 1000; self.latencies_ms.append(latency); self.usage[agent_id] += 1
            record = {"agent_id": agent_id, "success": result.success, "latency_ms": round(latency, 3), "hop": hop}
            self.recent_activity.append(record)
            if not result.success:
                self.failures[agent_id] += 1; self._emit(EventType.AGENT_FAILED, {**record, "error": result.error}); return outputs, result
            outputs.append(result.output); self._emit(EventType.AGENT_COMPLETED, record)
        return outputs, None

    def diagnostics(self):
        return {"usage": dict(self.usage), "failures": dict(self.failures), "average_latency_ms": sum(self.latencies_ms) / len(self.latencies_ms) if self.latencies_ms else 0.0, "recent_activity": list(self.recent_activity), "max_agent_hops": self.max_agent_hops}

    def _emit(self, event_type, payload):
        if self.event_bus: self.event_bus.emit(event_type, "agents", payload)
