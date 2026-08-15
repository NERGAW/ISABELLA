"""Lifecycle contract for bounded runtime services."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import threading
from time import perf_counter, sleep
from typing import Callable


class ServiceState(str, Enum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    ONLINE = "ONLINE"
    DEGRADED = "DEGRADED"
    ERROR = "ERROR"
    STOPPING = "STOPPING"


Hook = Callable[[], object]


@dataclass
class Service:
    name: str
    dependencies: tuple[str, ...] = ()
    required: bool = False
    start_hook: Hook = lambda: True
    stop_hook: Hook = lambda: True
    health_hook: Hook = lambda: True
    bounded: bool = True
    state: ServiceState = ServiceState.STOPPED
    restart_attempts: int = 0
    last_error: str | None = None
    startup_ms: float = 0.0
    shutdown_ms: float = 0.0
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def start(self, timeout: float) -> bool:
        with self._lock:
            if self.state in {ServiceState.ONLINE, ServiceState.DEGRADED}:
                return True
            self.state = ServiceState.STARTING
        started = perf_counter()
        completed, result, error = self._invoke(self.start_hook, timeout)
        self.startup_ms = (perf_counter() - started) * 1000
        with self._lock:
            if not completed or error or result is False:
                self.state = ServiceState.ERROR
                self.last_error = "TimeoutError" if not completed else type(error).__name__ if error else "start_returned_false"
                return False
            self.state = ServiceState.ONLINE
            self.last_error = None
            return True

    def stop(self, timeout: float) -> bool:
        with self._lock:
            if self.state is ServiceState.STOPPED:
                return True
            self.state = ServiceState.STOPPING
        started = perf_counter()
        completed, result, error = self._invoke(self.stop_hook, timeout)
        self.shutdown_ms = (perf_counter() - started) * 1000
        with self._lock:
            if not completed or error or result is False:
                self.state = ServiceState.ERROR
                self.last_error = "TimeoutError" if not completed else type(error).__name__ if error else "stop_returned_false"
                return False
            self.state = ServiceState.STOPPED
            return True

    def health_check(self) -> ServiceState:
        try:
            result = self.health_hook()
            if isinstance(result, ServiceState):
                state = result
            elif isinstance(result, str) and result in ServiceState._value2member_map_:
                state = ServiceState(result)
            else:
                state = ServiceState.ONLINE if result else ServiceState.DEGRADED
        except Exception as exc:
            self.last_error = type(exc).__name__
            state = ServiceState.ERROR
        with self._lock:
            self.state = state
        return state

    def restart(self, timeout: float, max_attempts: int, cooldown: float) -> bool:
        if self.restart_attempts >= max_attempts:
            return False
        self.restart_attempts += 1
        self.stop(timeout)
        if cooldown > 0:
            sleep(cooldown)
        return self.start(timeout)

    @staticmethod
    def _bounded(hook: Hook, timeout: float):
        result = []
        error = []

        def invoke():
            try:
                result.append(hook())
            except BaseException as exc:
                error.append(exc)

        thread = threading.Thread(target=invoke, name="IsabellaServiceHook", daemon=True)
        thread.start()
        thread.join(timeout)
        return not thread.is_alive(), result[0] if result else None, error[0] if error else None

    def _invoke(self, hook: Hook, timeout: float):
        if self.bounded:
            return self._bounded(hook, timeout)
        try:
            return True, hook(), None
        except BaseException as exc:
            return True, None, exc
