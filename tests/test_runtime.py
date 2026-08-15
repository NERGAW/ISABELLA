import threading
from time import perf_counter

import pytest
import psutil

from Isabella.Runtime import ApplicationRuntime, IsabellaRuntime, Service, ServiceRegistry, ServiceState


def config(**overrides):
    values = {
        "startup_timeout_seconds": 0.2,
        "shutdown_timeout_seconds": 0.2,
        "restart_attempts": 2,
        "restart_cooldown_seconds": 0,
        "enabled_services": [],
    }
    values.update(overrides)
    return values


class Bus:
    def __init__(self):
        self.events = []

    def emit(self, event_type, source, payload):
        self.events.append((event_type.value, source, payload))


def test_dependency_order_is_topological():
    registry = ServiceRegistry()
    registry.register(Service("HUD", ("Core", "Brain")))
    registry.register(Service("Brain", ("Core",)))
    registry.register(Service("Core", required=True))
    assert [service.name for service in registry.startup_order()] == ["Core", "Brain", "HUD"]


def test_missing_and_cyclic_dependencies_are_rejected():
    registry = ServiceRegistry()
    registry.register(Service("A", ("missing",)))
    with pytest.raises(ValueError, match="Missing dependencies"):
        registry.startup_order()
    cyclic = ServiceRegistry()
    cyclic.register(Service("A", ("B",)))
    cyclic.register(Service("B", ("A",)))
    with pytest.raises(ValueError, match="Cyclic"):
        cyclic.startup_order()


def test_required_failure_rolls_back_started_services():
    calls = []
    runtime = IsabellaRuntime(config(enabled_services=["Core", "Required"]))
    runtime.register(Service("Core", required=True, start_hook=lambda: calls.append("start-core") or True, stop_hook=lambda: calls.append("stop-core") or True))
    runtime.register(Service("Required", ("Core",), required=True, start_hook=lambda: False))
    assert not runtime.start()
    assert runtime.state is ServiceState.ERROR
    assert calls == ["start-core", "stop-core"]


def test_optional_failures_produce_degraded_mode_without_crash():
    runtime = IsabellaRuntime(config(enabled_services=["Core", "Vision", "Voice", "TTS", "Intelligence"]))
    runtime.register(Service("Core", required=True))
    for name in ("Vision", "Voice", "TTS", "Intelligence"):
        runtime.register(Service(name, ("Core",), start_hook=lambda: False))
    assert runtime.start()
    assert runtime.state is ServiceState.DEGRADED
    assert runtime.registry.get("Core").state is ServiceState.ONLINE
    assert all(runtime.registry.get(name).state is ServiceState.ERROR for name in ("Vision", "Voice", "TTS", "Intelligence"))
    runtime.shutdown()


def test_degraded_health_does_not_fail_startup():
    runtime = IsabellaRuntime(config(enabled_services=["Core", "Intelligence"]))
    runtime.register(Service("Core", required=True))
    runtime.register(Service("Intelligence", ("Core",), health_hook=lambda: ServiceState.DEGRADED))
    assert runtime.start()
    assert runtime.state is ServiceState.DEGRADED
    runtime.shutdown()


def test_restart_is_local_and_limited():
    attempts = []
    runtime = IsabellaRuntime(config(enabled_services=["Core", "TTS"], restart_attempts=2))
    runtime.register(Service("Core", required=True))
    runtime.register(Service("TTS", ("Core",), start_hook=lambda: (attempts.append(1) or len(attempts) >= 2)))
    runtime.start()
    assert runtime.restart_service("TTS")
    assert runtime.restart_service("TTS")
    assert not runtime.restart_service("TTS")
    assert runtime.registry.get("Core").state is ServiceState.ONLINE
    assert len(attempts) == 3
    runtime.shutdown()


def test_restart_limit_after_repeated_failure():
    runtime = IsabellaRuntime(config(enabled_services=["Core", "Voice"], restart_attempts=2))
    runtime.register(Service("Core", required=True))
    runtime.register(Service("Voice", ("Core",), start_hook=lambda: False))
    runtime.start()
    assert not runtime.restart_service("Voice")
    assert not runtime.restart_service("Voice")
    assert not runtime.restart_service("Voice")
    assert runtime.registry.get("Voice").restart_attempts == 2
    runtime.shutdown()


def test_shutdown_is_reverse_dependency_order_with_event_bus_last():
    calls = []
    runtime = IsabellaRuntime(config(enabled_services=["Core", "Event Bus", "Brain", "HUD"]))
    runtime.register(Service("Core", required=True, stop_hook=lambda: calls.append("Core") or True))
    runtime.register(Service("Event Bus", ("Core",), required=True, stop_hook=lambda: calls.append("Event Bus") or True))
    runtime.register(Service("Brain", ("Event Bus",), stop_hook=lambda: calls.append("Brain") or True))
    runtime.register(Service("HUD", ("Brain",), stop_hook=lambda: calls.append("HUD") or True))
    assert runtime.start()
    assert runtime.shutdown()
    assert calls == ["HUD", "Brain", "Core", "Event Bus"]


def test_timeout_does_not_block_runtime_indefinitely():
    release = threading.Event()
    service = Service("slow", start_hook=lambda: release.wait(10))
    started = perf_counter()
    assert not service.start(0.05)
    elapsed = perf_counter() - started
    release.set()
    assert elapsed < 0.2
    assert service.state is ServiceState.ERROR
    assert service.last_error == "TimeoutError"


def test_runtime_events_and_status_report():
    bus = Bus()
    runtime = IsabellaRuntime(config(enabled_services=["Core"]), event_bus=bus)
    runtime.register(Service("Core", required=True))
    runtime.start()
    report = runtime.report()
    runtime.shutdown()
    names = [event[0] for event in bus.events]
    assert report["runtime"] == "ONLINE"
    assert report["services"]["Core"]["required"] is True
    assert {"service.starting", "service.online", "runtime.started", "runtime.stopping", "service.stopped", "runtime.stopped"} <= set(names)


def test_ten_start_stop_cycles_leave_no_runtime_threads():
    baseline = {thread.ident for thread in threading.enumerate()}
    for _ in range(10):
        runtime = IsabellaRuntime(config(enabled_services=["Core"]))
        runtime.register(Service("Core", required=True))
        assert runtime.start()
        assert runtime.shutdown()
    remaining = [thread for thread in threading.enumerate() if thread.ident not in baseline and thread.name.startswith("Isabella")]
    assert remaining == []


def test_application_modes_share_runtime_and_cli_omits_hud():
    gui = ApplicationRuntime(config(enabled_services=["Core", "Event Bus", "Intelligence", "HUD"]), mode="gui")
    cli = ApplicationRuntime(config(enabled_services=["Core", "Event Bus", "Intelligence", "HUD"]), mode="cli")
    assert gui.registry.get("HUD") is not None
    assert cli.registry.get("HUD") is None
    assert {"Core", "Event Bus", "Intelligence"} <= {service.name for service in gui.registry.list()}
    assert {"Core", "Event Bus", "Intelligence"} <= {service.name for service in cli.registry.list()}


def test_fifty_interactions_keep_resources_bounded():
    process = psutil.Process()
    initial_ram = process.memory_info().rss
    baseline_threads = threading.active_count()
    handled = []
    runtime = IsabellaRuntime(config(enabled_services=["Core", "Commands"]))
    runtime.register(Service("Core", required=True))
    runtime.register(Service("Commands", ("Core",)))
    assert runtime.start()
    for index in range(50):
        assert runtime.accepting_commands
        handled.append(f"interaction-{index}")
    assert runtime.shutdown()
    assert len(handled) == 50
    assert threading.active_count() == baseline_threads
    assert process.memory_info().rss - initial_ram < 25 * 1024 * 1024
