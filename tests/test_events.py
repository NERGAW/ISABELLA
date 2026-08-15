import threading
from time import perf_counter

from Isabella.Events import Event, EventBus, EventPriority, EventType


def config(**overrides):
    values = {
        "enabled": True,
        "queue_max_size": 20,
        "worker_count": 2,
        "high_priority_reserve": 3,
        "shutdown_timeout_seconds": 2.0,
    }
    values.update(overrides)
    return values


def test_event_envelope_has_identity_time_payload_and_priority():
    event = Event("ui.message", "test", {"ok": True}, "request-1", EventPriority.HIGH)
    assert event.id and event.timestamp
    assert event.payload == {"ok": True}
    assert event.correlation_id == "request-1"
    assert event.priority is EventPriority.HIGH


def test_specific_category_global_and_unsubscribe():
    bus = EventBus(config())
    received = []
    exact = lambda event: received.append(("exact", event.type))
    category = lambda event: received.append(("category", event.type))
    global_subscriber = lambda event: received.append(("global", event.type))
    bus.subscribe(EventType.SKILL_COMPLETED.value, exact)
    bus.subscribe("skill.*", category)
    bus.subscribe("*", global_subscriber)
    assert bus.emit(EventType.SKILL_COMPLETED, "test")
    assert bus.wait_until_idle()
    assert {item[0] for item in received} == {"exact", "category", "global"}
    assert bus.unsubscribe(EventType.SKILL_COMPLETED.value, exact)
    assert not bus.unsubscribe(EventType.SKILL_COMPLETED.value, exact)
    bus.shutdown()


def test_subscriber_failure_is_isolated_and_publish_is_nonblocking():
    bus = EventBus(config(worker_count=1))
    release = threading.Event()
    completed = threading.Event()

    def slow(_event):
        release.wait(1)

    def broken(_event):
        raise RuntimeError("subscriber failed")

    bus.subscribe("ui.message", slow)
    bus.subscribe("ui.message", broken)
    bus.subscribe("ui.message", lambda _event: completed.set())
    started = perf_counter()
    assert bus.emit(EventType.UI_MESSAGE, "test")
    assert (perf_counter() - started) * 1000 < 50
    release.set()
    assert completed.wait(1)
    assert bus.wait_until_idle()
    assert bus.diagnostics()["failed_count"] == 1
    bus.shutdown()


def test_bounded_queue_preserves_reserved_capacity_for_system_error():
    bus = EventBus(config(queue_max_size=10, worker_count=1, high_priority_reserve=2))
    entered = threading.Event()
    release = threading.Event()

    def blocked(_event):
        entered.set()
        release.wait(1)

    bus.subscribe("*", blocked)
    bus.emit(EventType.UI_MESSAGE, "test")
    assert entered.wait(1)
    normal_results = [bus.emit(EventType.UI_MESSAGE, "test") for _ in range(12)]
    assert False in normal_results
    assert bus.emit(EventType.SYSTEM_ERROR, "test", priority=EventPriority.HIGH)
    release.set()
    assert bus.wait_until_idle()
    assert bus.diagnostics()["dropped_count"] > 0
    bus.shutdown()


def test_correlation_propagates_to_events_emitted_by_subscribers():
    bus = EventBus(config(worker_count=1))
    correlations = []
    bus.subscribe("voice.command", lambda _event: bus.emit(EventType.CONTEXT_UPDATED, "context"))
    bus.subscribe("context.updated", lambda event: correlations.append(event.correlation_id))
    bus.emit(EventType.VOICE_COMMAND, "voice", correlation_id="voice-42")
    assert bus.wait_until_idle()
    assert correlations == ["voice-42"]
    bus.shutdown()


def test_parallel_publishers_and_fifty_interactions_leave_no_queue_growth():
    bus = EventBus(config(queue_max_size=200, worker_count=2, high_priority_reserve=10))
    received = []
    lock = threading.Lock()

    def collect(event):
        with lock:
            received.append(event.id)

    bus.subscribe("brain.*", collect)

    def publish_batch(offset):
        for index in range(10):
            bus.emit(EventType.BRAIN_COMPLETED, "test", correlation_id=f"req-{offset + index}")

    threads = [threading.Thread(target=publish_batch, args=(index * 10,)) for index in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert bus.wait_until_idle()
    assert len(received) == 50
    assert bus.diagnostics()["queue_size"] == 0
    assert bus.diagnostics()["average_publish_ms"] < 50
    assert bus.shutdown()


def test_shutdown_stops_fixed_workers_and_rejects_new_events():
    bus = EventBus(config(worker_count=2))
    assert len(bus._threads) == 2
    assert bus.shutdown()
    assert not any(thread.is_alive() for thread in bus._threads)
    assert not bus.emit(EventType.UI_MESSAGE, "test")
