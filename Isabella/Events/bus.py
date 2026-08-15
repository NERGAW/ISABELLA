"""Bounded asynchronous event bus with a fixed worker pool."""

from __future__ import annotations

import json
import logging
import queue
import threading
from collections import deque
from itertools import count
from pathlib import Path
from time import monotonic, perf_counter
from typing import Callable, Any

from Isabella.Core.config import ConfigurationError, PROJECT_ROOT
from .models import Event, EventPriority, get_correlation_id, reset_correlation_id, set_correlation_id
from .types import EventType


LOGGER = logging.getLogger("EVENTS")
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "events.json"
Subscriber = Callable[[Event], object]


def load_events_config(path: Path | None = None) -> dict[str, Any]:
    target = path or DEFAULT_CONFIG_PATH
    try:
        config = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Invalid events configuration: {target}") from exc
    required = {"enabled", "queue_max_size", "worker_count", "high_priority_reserve", "shutdown_timeout_seconds"}
    if not isinstance(config, dict) or required - config.keys():
        raise ConfigurationError("Events configuration is missing required fields")
    if int(config["queue_max_size"]) < 10 or not 1 <= int(config["worker_count"]) <= 8:
        raise ConfigurationError("Event Bus limits are invalid")
    if not 0 <= int(config["high_priority_reserve"]) < int(config["queue_max_size"]):
        raise ConfigurationError("Event Bus priority reserve is invalid")
    return config


class EventBus:
    def __init__(self, config: dict[str, Any]) -> None:
        self.enabled = bool(config.get("enabled", True))
        self.max_size = int(config["queue_max_size"])
        self.worker_count = int(config["worker_count"])
        self.high_priority_reserve = int(config["high_priority_reserve"])
        self.shutdown_timeout = float(config["shutdown_timeout_seconds"])
        self._queue: queue.PriorityQueue[tuple[int, int, Event]] = queue.PriorityQueue(maxsize=self.max_size)
        self._subscribers: dict[str, set[Subscriber]] = {}
        self._lock = threading.RLock()
        self._sequence = count()
        self._stop = threading.Event()
        self._accepting = self.enabled
        self._threads: list[threading.Thread] = []
        self.publish_latencies_ms: deque[float] = deque(maxlen=200)
        self.processed_count = 0
        self.failed_count = 0
        self.dropped_count = 0
        if self.enabled:
            for index in range(self.worker_count):
                thread = threading.Thread(
                    target=self._worker, name=f"IsabellaEventBus-{index + 1}", daemon=True,
                )
                thread.start()
                self._threads.append(thread)

    @classmethod
    def from_config(cls, path: Path | None = None) -> "EventBus":
        return cls(load_events_config(path))

    def subscribe(self, event_pattern: str, subscriber: Subscriber) -> None:
        if not event_pattern or not callable(subscriber):
            raise ValueError("A pattern and callable subscriber are required")
        with self._lock:
            self._subscribers.setdefault(event_pattern, set()).add(subscriber)

    def unsubscribe(self, event_pattern: str, subscriber: Subscriber) -> bool:
        with self._lock:
            subscribers = self._subscribers.get(event_pattern)
            if not subscribers or subscriber not in subscribers:
                return False
            subscribers.remove(subscriber)
            if not subscribers:
                self._subscribers.pop(event_pattern, None)
            return True

    def publish(self, event: Event) -> bool:
        started = perf_counter()
        try:
            if not self._accepting:
                with self._lock:
                    self.dropped_count += 1
                return False
            high_priority = event.priority == EventPriority.HIGH or event.type == EventType.SYSTEM_ERROR.value
            if not high_priority and self._queue.qsize() >= self.max_size - self.high_priority_reserve:
                with self._lock:
                    self.dropped_count += 1
                LOGGER.warning("queue_full dropped type=%s", event.type)
                return False
            try:
                self._queue.put_nowait((int(event.priority), next(self._sequence), event))
                return True
            except queue.Full:
                with self._lock:
                    self.dropped_count += 1
                LOGGER.warning("queue_full dropped type=%s", event.type)
                return False
        finally:
            self.publish_latencies_ms.append((perf_counter() - started) * 1000)

    def emit(
        self, event_type: EventType | str, source: str, payload: dict[str, Any] | None = None,
        *, correlation_id: str | None = None, priority: EventPriority = EventPriority.NORMAL,
    ) -> bool:
        name = event_type.value if isinstance(event_type, EventType) else event_type
        return self.publish(Event(name, source, payload or {}, correlation_id or get_correlation_id(), priority))

    def _worker(self) -> None:
        while not self._stop.is_set() or not self._queue.empty():
            try:
                _, _, event = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                token = set_correlation_id(event.correlation_id)
                try:
                    for subscriber in self._matching_subscribers(event.type):
                        try:
                            subscriber(event)
                        except Exception:
                            with self._lock:
                                self.failed_count += 1
                            LOGGER.exception("subscriber_failed type=%s", event.type)
                finally:
                    reset_correlation_id(token)
                with self._lock:
                    self.processed_count += 1
                LOGGER.debug("type=%s source=%s correlation_id=%s", event.type, event.source, event.correlation_id)
            finally:
                self._queue.task_done()

    def _matching_subscribers(self, event_type: str) -> tuple[Subscriber, ...]:
        category = event_type.split(".", 1)[0]
        with self._lock:
            matched = set(self._subscribers.get(event_type, ()))
            matched.update(self._subscribers.get(f"{category}.*", ()))
            matched.update(self._subscribers.get(category, ()))
            matched.update(self._subscribers.get("*", ()))
        return tuple(matched)

    def clear(self) -> int:
        removed = 0
        while True:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
                removed += 1
            except queue.Empty:
                break
        return removed

    def diagnostics(self) -> dict[str, int | float]:
        with self._lock:
            subscriber_count = sum(len(items) for items in self._subscribers.values())
            processed, failed, dropped = self.processed_count, self.failed_count, self.dropped_count
        average = sum(self.publish_latencies_ms) / len(self.publish_latencies_ms) if self.publish_latencies_ms else 0.0
        return {
            "subscriber_count": subscriber_count,
            "queue_size": self._queue.qsize(),
            "processed_count": processed,
            "failed_count": failed,
            "dropped_count": dropped,
            "average_publish_ms": average,
        }

    def wait_until_idle(self, timeout: float = 5.0) -> bool:
        deadline = monotonic() + timeout
        while self._queue.unfinished_tasks and monotonic() < deadline:
            self._stop.wait(0.01)
        return self._queue.unfinished_tasks == 0

    def shutdown(self) -> bool:
        self._accepting = False
        drained = self.wait_until_idle(self.shutdown_timeout)
        self._stop.set()
        for thread in self._threads:
            thread.join(self.shutdown_timeout)
        with self._lock:
            self._subscribers.clear()
        return drained and not any(thread.is_alive() for thread in self._threads)
