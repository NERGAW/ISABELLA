"""Event-driven automation execution with cooldown and chain protection."""

from __future__ import annotations

from collections import OrderedDict
import threading
from time import monotonic
from typing import Callable

from Isabella.Events import Event, EventType
from .conditions import all_match
from .models import Automation, AutomationRun, TriggerType, now_iso


class AutomationEngine:
    def __init__(self, *, registry, event_bus, list_automations: Callable[[], list[Automation]], save: Callable[[Automation], None], max_chain_depth: int = 5, correlation_cache_size: int = 1000) -> None:
        self.registry = registry
        self.event_bus = event_bus
        self.list_automations = list_automations
        self.save = save
        self.max_chain_depth = max_chain_depth
        self.correlation_cache_size = correlation_cache_size
        self._last_runs: dict[str, float] = {}
        self._active: set[tuple[str, str]] = set()
        self._chain_depths: OrderedDict[str, int] = OrderedDict()
        self._lock = threading.RLock()
        self._started = False
        self.failures = 0
        self.last_execution: str | None = None

    def start(self) -> bool:
        if self._started or self.event_bus is None:
            return self._started
        self.event_bus.subscribe("*", self.handle_event)
        self._started = True
        return True

    def shutdown(self) -> bool:
        if self._started and self.event_bus:
            self.event_bus.unsubscribe("*", self.handle_event)
        self._started = False
        return True

    def handle_event(self, event: Event) -> None:
        for automation in self.list_automations():
            if not automation.enabled or automation.trigger.type is TriggerType.MANUAL:
                continue
            if automation.trigger.event != event.type or not all_match(automation.conditions, event.payload):
                continue
            self.run(automation, event.payload, event.correlation_id or event.id)

    def run(self, automation: Automation, payload: dict | None = None, correlation_id: str | None = None) -> AutomationRun:
        correlation = correlation_id or f"manual:{automation.id}:{monotonic()}"
        key = (automation.id, correlation)
        now = monotonic()
        with self._lock:
            if not automation.enabled:
                return AutomationRun(automation.id, False, (), "AUTOMATION_DISABLED")
            if automation.max_runs is not None and automation.run_count >= automation.max_runs:
                return AutomationRun(automation.id, False, (), "MAX_RUNS_REACHED")
            if now - self._last_runs.get(automation.id, float("-inf")) < automation.cooldown_seconds:
                return AutomationRun(automation.id, False, (), "COOLDOWN_ACTIVE")
            if key in self._active:
                self._emit(EventType.AUTOMATION_LOOP_BLOCKED, automation, correlation, "REENTRANCY_BLOCKED")
                return AutomationRun(automation.id, False, (), "REENTRANCY_BLOCKED")
            depth = self._chain_depths.get(correlation, 0) + 1
            self._chain_depths[correlation] = depth
            self._chain_depths.move_to_end(correlation)
            while len(self._chain_depths) > self.correlation_cache_size:
                self._chain_depths.popitem(last=False)
            if depth > self.max_chain_depth:
                self._emit(EventType.AUTOMATION_LOOP_BLOCKED, automation, correlation, "MAX_CHAIN_DEPTH")
                return AutomationRun(automation.id, False, (), "MAX_CHAIN_DEPTH")
            self._active.add(key)
            self._last_runs[automation.id] = now
        self._emit(EventType.AUTOMATION_TRIGGERED, automation, correlation)
        automation.run_count += 1
        automation.last_run = now_iso()
        automation.updated_at = automation.last_run
        self.last_execution = automation.last_run
        self.save(automation)
        self._emit(EventType.AUTOMATION_STARTED, automation, correlation)
        results = []
        try:
            for action in automation.actions:
                # No `confirmed` flag or confirmation id is supplied. Security remains authoritative.
                result = self.registry.execute(
                    action.skill, dict(action.arguments),
                    source_request_id=f"automation:{automation.id}",
                )
                results.append(result.to_dict())
                if not result.success:
                    self.failures += 1
                    self._emit(EventType.AUTOMATION_FAILED, automation, correlation, result.error_code or "ACTION_FAILED")
                    return AutomationRun(automation.id, False, tuple(results), result.error_code or "ACTION_FAILED")
            self._emit(EventType.AUTOMATION_COMPLETED, automation, correlation)
            return AutomationRun(automation.id, True, tuple(results))
        finally:
            with self._lock:
                self._active.discard(key)

    def _emit(self, event_type, automation: Automation, correlation: str, error: str | None = None) -> None:
        if self.event_bus:
            payload = {"automation_id": automation.id, "run_count": automation.run_count}
            if error:
                payload["error"] = error
            self.event_bus.emit(event_type, "automations", payload, correlation_id=correlation)
