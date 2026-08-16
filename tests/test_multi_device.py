from datetime import datetime, timedelta, timezone

import pytest

from Isabella.Events import EventType
from Isabella.Notifications import Notification, NotificationManager, NotificationType
from Isabella.Sessions import SessionManager


class Bus:
    def __init__(self): self.events = []
    def emit(self, kind, source, payload=None, **kwargs):
        self.events.append(kind.value if hasattr(kind, "value") else kind); return True


class Context:
    def __init__(self): self.metadata = {}
    def get(self, name, default=None): return self.metadata if name == "metadata" else default
    def set(self, name, value): self.metadata = value


def test_session_continuation_and_handoff_updates_context():
    bus, context = Bus(), Context()
    sessions = SessionManager(context=context, event_bus=bus)
    session = sessions.create("primary.local", {"topic": "ISABELLA"})
    with pytest.raises(PermissionError):
        sessions.resolve("mobile.wrong", session.session_id)
    sessions.handoff_session(session.session_id, "mobile.one")
    same = sessions.resolve("mobile.one", session.session_id)
    assert same.session_id == session.session_id and same.active_node == "mobile.one"
    handed = sessions.handoff_session(session.session_id, "primary.local")
    assert handed.working_memory_reference == session.working_memory_reference
    assert context.metadata["active_node"] == "primary.local"
    assert EventType.SESSION_CREATED.value in bus.events and EventType.SESSION_HANDOFF.value in bus.events


def test_notification_delivery_dedup_offline_reconnect_and_quiet_mode():
    bus = Bus(); sent = []
    manager = NotificationManager(event_bus=bus, queue_limit=2)
    warning = Notification(NotificationType.WARNING, "LLM", "Ollama offline", "diagnostics", target_node="mobile.one", id="same")
    assert not manager.create(warning)
    assert not manager.create(warning)
    assert manager.diagnostics()["offline_queued"] == 1
    manager.bind_sender(lambda node, item: sent.append((node, item.id)) or True)
    assert manager.flush("mobile.one") == 1 and sent == [("mobile.one", "same")]
    manager.set_preferences("mobile.one", {}, quiet=True)
    assert not manager.create(Notification(NotificationType.INFO, "Info", "silenciosa", "test", target_node="mobile.one"))
    assert EventType.NOTIFICATION_CREATED.value in bus.events


def test_actionable_notification_rejects_wrong_node_expired_and_unknown_action():
    called = []
    manager = NotificationManager()
    manager.bind_action_handler(lambda item, node, action: called.append(action))
    valid = Notification(NotificationType.ACTION_REQUIRED, "Confirmar", "Desligar?", "security",
                         actions=("Confirmar", "Cancelar"), target_node="mobile.one")
    assert not manager.create(valid)
    with pytest.raises(PermissionError): manager.act(valid.id, "mobile.wrong", "Confirmar")
    with pytest.raises(PermissionError): manager.act(valid.id, "mobile.one", "Abrir")
    manager.act(valid.id, "mobile.one", "Cancelar")
    expired = Notification(NotificationType.ACTION_REQUIRED, "Expirada", "Nada", "security", actions=("Confirmar",),
                           target_node="mobile.one", expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
    assert not manager.create(expired)
    with pytest.raises(PermissionError): manager.act(expired.id, "mobile.one", "Confirmar")
    assert called == ["Cancelar"]
