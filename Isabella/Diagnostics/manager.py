"""Central read-only health aggregation with optional periodic monitoring."""

from __future__ import annotations

from collections import deque
import json
from pathlib import Path
import threading
from time import monotonic, perf_counter
from typing import Any

import psutil

from Isabella.Core.config import ConfigurationError, PROJECT_ROOT
from Isabella.Events import EventType
from .health import health, latency_details, path_size, queue_size
from .models import DiagnosticsReport, FailureRecord, HealthStatus, Subsystem, SystemMetrics, now_iso


DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "diagnostics.json"


def load_diagnostics_config(path: Path | None = None) -> dict[str, Any]:
    target = path or DEFAULT_CONFIG_PATH
    try:
        config = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Invalid diagnostics configuration: {target}") from exc
    required = {"enabled", "check_interval_seconds", "expensive_check_interval_seconds", "failure_history_limit"}
    if not isinstance(config, dict) or required - config.keys():
        raise ConfigurationError("Diagnostics configuration is missing required fields")
    if not 5 <= float(config["check_interval_seconds"]) <= 300:
        raise ConfigurationError("Diagnostics interval is invalid")
    if float(config["expensive_check_interval_seconds"]) < float(config["check_interval_seconds"]):
        raise ConfigurationError("Expensive diagnostics interval is invalid")
    if not 1 <= int(config["failure_history_limit"]) <= 500:
        raise ConfigurationError("Diagnostics history limit is invalid")
    return config


class DiagnosticsManager:
    def __init__(self, config: dict[str, Any], *, app=None, brain=None, controller=None, event_bus=None, runtime=None) -> None:
        self.config = config
        self.enabled = bool(config["enabled"])
        self.app = app
        self.brain = brain
        self.controller = controller
        self.event_bus = event_bus or getattr(app, "event_bus", None)
        self.runtime = runtime
        self.failure_history: deque[FailureRecord] = deque(maxlen=int(config["failure_history_limit"]))
        self.check_latencies_ms: deque[float] = deque(maxlen=200)
        self._statuses: dict[Subsystem, HealthStatus] = {}
        self._started_at = monotonic()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()

    @classmethod
    def from_config(cls, path: Path | None = None, **components) -> "DiagnosticsManager":
        return cls(load_diagnostics_config(path), **components)

    def bind(self, *, app=None, brain=None, controller=None, event_bus=None, runtime=None) -> None:
        if app is not None:
            self.app = app
        if brain is not None:
            self.brain = brain
        if controller is not None:
            self.controller = controller
        if event_bus is not None:
            self.event_bus = event_bus
        if runtime is not None:
            self.runtime = runtime

    def check(self, detailed: bool = False, expensive: bool = False) -> DiagnosticsReport:
        started = perf_counter()
        statuses = {item.value: self._check_subsystem(item, expensive) for item in Subsystem}
        metrics = self._system_metrics()
        summary = self._summary(statuses)
        self.check_latencies_ms.append((perf_counter() - started) * 1000)
        return DiagnosticsReport(statuses, metrics, summary, detailed)

    def _check_subsystem(self, subsystem: Subsystem, expensive: bool):
        brain, app, controller = self.brain, self.app, self.controller

        def probe():
            if subsystem is Subsystem.CORE:
                value = getattr(getattr(app, "status", None), "value", None)
                runtime_state = getattr(getattr(self.runtime, "state", None), "value", "UNKNOWN")
                runtime_services = {
                    name: item["state"] for name, item in self.runtime.report()["services"].items()
                } if self.runtime else {}
                return (HealthStatus.ONLINE if value == "ONLINE" else HealthStatus.OFFLINE if value == "OFFLINE" else HealthStatus.UNKNOWN), {"state": value or "UNKNOWN", "runtime_state": runtime_state, "runtime_services": runtime_services}
            if subsystem is Subsystem.LLM:
                llm = getattr(brain, "llm", None)
                if llm is None:
                    return HealthStatus.OFFLINE, {}
                reachable = bool(llm.health_check())
                can_list_models = hasattr(llm, "list_models")
                models = llm.list_models() if reachable and expensive and can_list_models else []
                model = getattr(llm, "model", None)
                available = None if not expensive or not can_list_models else any(name == model or name.split(":")[0] == str(model).split(":")[0] for name in models)
                details = {"reachable": reachable, "model": model, "model_available": available, **latency_details(llm), "recent_errors": len(getattr(llm, "recent_errors", ())) }
                return (HealthStatus.ONLINE if reachable and available is not False else HealthStatus.DEGRADED if reachable else HealthStatus.OFFLINE), details
            if subsystem in {Subsystem.ROUTER, Subsystem.PLANNER}:
                component = getattr(brain, subsystem.value.lower(), None)
                return (HealthStatus.ONLINE if component else HealthStatus.OFFLINE), latency_details(component) if component else {}
            if subsystem is Subsystem.SKILLS:
                registry = getattr(brain, "registry", None)
                count = len(registry.list()) if registry and hasattr(registry, "list") else 0
                return (HealthStatus.ONLINE if count else HealthStatus.OFFLINE), {"registered": count}
            if subsystem is Subsystem.VOICE_INPUT:
                listener = getattr(app, "voice_listener", None)
                if listener is None:
                    return HealthStatus.OFFLINE, {"listener_state": "OFFLINE"}
                state = getattr(getattr(listener, "state", None), "value", str(getattr(listener, "state", "UNKNOWN")))
                status = HealthStatus.ERROR if state == "ERROR" else HealthStatus.OFFLINE if state == "STOPPED" else HealthStatus.ONLINE
                return status, {"listener_state": state, "microphone": status is HealthStatus.ONLINE, "stt_model_loaded": getattr(getattr(listener, "stt", None), "_model", None) is not None, "queue_size": queue_size(listener, "_audio_queue")}
            if subsystem is Subsystem.VOICE_OUTPUT:
                tts = getattr(app, "tts_manager", None)
                if tts is None:
                    return HealthStatus.OFFLINE, {"provider": None, "queue_size": 0}
                state = getattr(tts, "state", "UNKNOWN")
                healthy = bool(tts.health_check()) if hasattr(tts, "health_check") else state not in {"ERROR", "STOPPED"}
                status = HealthStatus.ERROR if state == "ERROR" else HealthStatus.ONLINE if healthy else HealthStatus.DEGRADED
                provider = getattr(getattr(tts, "_active_provider", None), "name", None)
                return status, {"state": state, "provider": provider, "queue_size": queue_size(tts, "_queue")}
            if subsystem is Subsystem.HUD:
                if controller is None:
                    return HealthStatus.OFFLINE, {}
                state = getattr(getattr(controller, "state", None), "value", "UNKNOWN")
                return (HealthStatus.ERROR if state == "ERROR" else HealthStatus.ONLINE), {"state": state}
            if subsystem is Subsystem.MEMORY:
                memory = getattr(brain, "memory", None)
                database = getattr(memory, "database", None)
                if memory is None or database is None:
                    return HealthStatus.ERROR if getattr(memory, "status", None) == "ERROR" else HealthStatus.OFFLINE, {}
                accessible = bool(database.health_check()) if hasattr(database, "health_check") else True
                details = {"database_accessible": accessible, "last_write": getattr(memory, "last_write_at", None), "last_read": getattr(memory, "last_read_at", None), "database_size_bytes": path_size(database.path)}
                return (HealthStatus.ONLINE if accessible else HealthStatus.ERROR), details
            if subsystem is Subsystem.CONTEXT:
                context = getattr(brain, "context", None)
                value = getattr(context, "status", "OFFLINE")
                return HealthStatus(value) if value in HealthStatus._value2member_map_ else HealthStatus.UNKNOWN, {}
            if subsystem is Subsystem.VISION:
                vision = getattr(brain, "vision", None)
                if vision is None:
                    return HealthStatus.OFFLINE, {}
                capabilities = vision.health_check(check_camera=expensive)
                status = HealthStatus.ONLINE if capabilities.get("screen") else HealthStatus.ERROR
                if capabilities.get("camera") is False and status is HealthStatus.ONLINE:
                    status = HealthStatus.DEGRADED
                return status, capabilities
            if subsystem is Subsystem.EVENT_BUS:
                bus = self.event_bus or getattr(app, "event_bus", None)
                if bus is None:
                    return HealthStatus.OFFLINE, {}
                details = bus.diagnostics()
                status = HealthStatus.DEGRADED if details.get("failed_count", 0) or details.get("dropped_count", 0) else HealthStatus.ONLINE
                return status, details
            if subsystem is Subsystem.SECURITY:
                security = getattr(brain, "security", None)
                if security is None:
                    return HealthStatus.OFFLINE, {"policy_loaded": False}
                security.expire_pending()
                return HealthStatus.ONLINE, {"policy_loaded": True, "pending_confirmations": security.pending_count, "expired_confirmations": security.expired_count}
            if subsystem is Subsystem.MCP:
                mcp = getattr(brain, "mcp", None)
                if mcp is None:
                    return HealthStatus.OFFLINE, {"enabled": False, "connected_servers": 0, "available_tools": 0, "recent_failures": 0}
                details = mcp.health_check()
                status = HealthStatus.DEGRADED if details.get("unhealthy_servers") or details.get("recent_failures") else HealthStatus.ONLINE
                return status, details
            if subsystem is Subsystem.RESEARCH:
                research = getattr(brain, "research", None)
                if research is None:
                    return HealthStatus.OFFLINE, {"enabled": False}
                details = research.health_check()
                status = HealthStatus.ONLINE if details.get("provider_configured") else HealthStatus.DEGRADED
                return status, details
            return HealthStatus.UNKNOWN, {}

        result = health(subsystem, probe)
        self._record_transition(result)
        return result

    def _record_transition(self, result) -> None:
        with self._lock:
            previous = self._statuses.get(result.subsystem)
            self._statuses[result.subsystem] = result.status
        if result.status in {HealthStatus.DEGRADED, HealthStatus.OFFLINE, HealthStatus.ERROR} and previous != result.status:
            reason = str(result.details.get("error") or result.details.get("state") or result.status.value)
            self.failure_history.append(FailureRecord(result.subsystem, result.status, now_iso(), reason))
        if previous is not None and previous != result.status and self.event_bus:
            self.event_bus.emit(
                EventType.DIAGNOSTICS_STATUS_CHANGED, "diagnostics",
                {"subsystem": result.subsystem.value, "previous": previous.value, "status": result.status.value},
            )

    def _system_metrics(self) -> SystemMetrics:
        process = psutil.Process()
        listener = getattr(self.app, "voice_listener", None)
        tts = getattr(self.app, "tts_manager", None)
        controller = self.controller
        bus = self.event_bus or getattr(self.app, "event_bus", None)
        return SystemMetrics(
            cpu_percent=psutil.cpu_percent(interval=None),
            system_ram_percent=psutil.virtual_memory().percent,
            process_memory_mb=process.memory_info().rss / (1024 * 1024),
            thread_count=threading.active_count(),
            queue_sizes={
                "voice": queue_size(listener, "_audio_queue"), "tts": queue_size(tts, "_queue"),
                "hud_workers": controller.thread_pool.activeThreadCount() if controller else 0,
                "events": (bus.diagnostics().get("queue_size", 0) if bus else 0),
            },
            uptime_seconds=max(0.0, monotonic() - self._started_at),
        )

    @staticmethod
    def _summary(statuses) -> str:
        failures = [item for item in statuses.values() if item.status not in {HealthStatus.ONLINE, HealthStatus.UNKNOWN}]
        if not failures:
            return "Todos os sistemas principais estão operacionais."
        names = [item.subsystem.value for item in failures]
        if len(names) == 1:
            return f"{names[0]} está indisponível ou degradado. Os demais sistemas estão funcionando."
        return f"{', '.join(names[:-1])} e {names[-1]} estão indisponíveis ou degradados. Os demais sistemas estão funcionando."

    def start(self) -> bool:
        """Start opt-in monitoring; the application never calls this automatically."""
        if not self.enabled or self._thread and self._thread.is_alive():
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="IsabellaDiagnostics", daemon=True)
        self._thread.start()
        return True

    def _loop(self) -> None:
        interval = float(self.config["check_interval_seconds"])
        expensive_interval = float(self.config["expensive_check_interval_seconds"])
        last_expensive = 0.0
        while not self._stop.is_set():
            now = monotonic()
            expensive = now - last_expensive >= expensive_interval
            self.check(expensive=expensive)
            if expensive:
                last_expensive = now
            self._stop.wait(interval)

    def shutdown(self) -> bool:
        self._stop.set()
        if self._thread:
            self._thread.join(2.0)
        return not bool(self._thread and self._thread.is_alive())

    @property
    def average_check_ms(self) -> float:
        return sum(self.check_latencies_ms) / len(self.check_latencies_ms) if self.check_latencies_ms else 0.0
