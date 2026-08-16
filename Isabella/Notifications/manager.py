"""Bounded notification routing, offline queue and action validation."""

from collections import deque
from datetime import datetime, timezone
import threading
from typing import Callable

from Isabella.Events import EventType
from .models import Notification, NotificationType


class NotificationManager:
    def __init__(self, *, event_bus=None, queue_limit=50, history_limit=100) -> None:
        self.event_bus = event_bus
        self.queue_limit = queue_limit
        self._pending: dict[str, deque[Notification]] = {}
        self._history: deque[Notification] = deque(maxlen=history_limit)
        self._known: set[str] = set()
        self._known_order: deque[str] = deque()
        self._known_limit = history_limit * 2
        self._sender: Callable[[str, Notification], bool] | None = None
        self._action_handler: Callable[[Notification, str, str], object] | None = None
        self._preferences: dict[str, dict[str, bool]] = {}
        self._quiet: set[str] = set()
        self._lock = threading.RLock()
        self._subscriptions = []

    def bind_sender(self, sender) -> None: self._sender = sender
    def bind_action_handler(self, handler) -> None: self._action_handler = handler

    def subscribe_sources(self, sessions) -> None:
        if not self.event_bus: return
        mapping = {
            EventType.SCHEDULER_REMINDER.value: (NotificationType.REMINDER, "Lembrete"),
            EventType.AUTOMATION_COMPLETED.value: (NotificationType.SUCCESS, "Automação concluída"),
            EventType.DIAGNOSTICS_STATUS_CHANGED.value: (NotificationType.WARNING, "Diagnóstico alterado"),
        }
        def consume(event):
            kind, title = mapping[event.type]
            session_id = event.payload.get("session_id")
            session = sessions.get(session_id) if session_id else None
            target = session.active_node if session and session.active_node != "primary.local" else None
            if not target:
                active = sessions.latest_remote()
                target = active.active_node if active else None
            message = str(event.payload.get("message") or event.payload.get("text") or event.payload.get("status") or title)
            self.create(Notification(kind, title, message, event.source, target_node=target,
                                     id=(event.correlation_id or event.id).replace("-", "")[:64]))
        for event_name in mapping:
            self.event_bus.subscribe(event_name, consume); self._subscriptions.append((event_name, consume))

    def shutdown(self) -> None:
        if self.event_bus:
            for event_name, callback in self._subscriptions: self.event_bus.unsubscribe(event_name, callback)
        self._subscriptions.clear()

    def create(self, notification: Notification) -> bool:
        with self._lock:
            if notification.id in self._known or notification.expired:
                return False
            if len(self._known_order) >= self._known_limit:
                self._known.discard(self._known_order.popleft())
            self._known.add(notification.id); self._known_order.append(notification.id); self._history.append(notification)
        self._emit(EventType.NOTIFICATION_CREATED, notification)
        target = notification.target_node
        if not target or target == "primary.local":
            return True
        if not self._allowed(target, notification):
            return False
        if self._sender and self._sender(target, notification):
            self._emit(EventType.NOTIFICATION_SENT, notification, {"target_node": target})
            return True
        if notification.type in {NotificationType.WARNING, NotificationType.ERROR, NotificationType.ACTION_REQUIRED, NotificationType.REMINDER}:
            with self._lock:
                queue = self._pending.setdefault(target, deque(maxlen=self.queue_limit)); queue.append(notification)
        return False

    def flush(self, node_id: str) -> int:
        sent = 0
        with self._lock: items = list(self._pending.get(node_id, ()))
        remaining = []
        for item in items:
            if item.expired:
                continue
            if self._sender and self._sender(node_id, item): sent += 1
            else: remaining.append(item)
        with self._lock:
            if remaining: self._pending[node_id] = deque(remaining, maxlen=self.queue_limit)
            else: self._pending.pop(node_id, None)
        return sent

    def acknowledge(self, notification_id: str, node_id: str) -> bool:
        notification = self._find(notification_id)
        valid = bool(notification and (not notification.target_node or notification.target_node == node_id))
        if valid: self._emit(EventType.NOTIFICATION_ACKNOWLEDGED, notification, {"node_id": node_id})
        return valid

    def act(self, notification_id: str, node_id: str, action: str):
        notification = self._find(notification_id)
        if not notification or notification.expired or notification.target_node != node_id or action not in notification.actions:
            raise PermissionError("Notification action is invalid, expired or belongs to another Node")
        self._emit(EventType.NOTIFICATION_ACTION, notification, {"node_id": node_id, "action": action})
        return self._action_handler(notification, node_id, action) if self._action_handler else None

    def set_preferences(self, node_id: str, values: dict[str, bool], quiet=False) -> None:
        allowed = {"informational", "warnings", "errors", "reminders", "security"}
        self._preferences[node_id] = {key: bool(value) for key, value in values.items() if key in allowed}
        if quiet: self._quiet.add(node_id)
        else: self._quiet.discard(node_id)

    def diagnostics(self) -> dict[str, int]:
        with self._lock: return {"history": len(self._history), "offline_queued": sum(map(len, self._pending.values())), "dedup_ids": len(self._known)}

    def _allowed(self, node_id, item) -> bool:
        if node_id in self._quiet and item.type in {NotificationType.INFO, NotificationType.SUCCESS}: return False
        key = {NotificationType.INFO: "informational", NotificationType.SUCCESS: "informational", NotificationType.WARNING: "warnings", NotificationType.ERROR: "errors", NotificationType.REMINDER: "reminders", NotificationType.ACTION_REQUIRED: "security"}[item.type]
        return self._preferences.get(node_id, {}).get(key, True)

    def _find(self, notification_id):
        with self._lock: return next((item for item in reversed(self._history) if item.id == notification_id), None)

    def _emit(self, kind, item, extra=None):
        if self.event_bus:
            payload = {"notification_id": item.id, "type": item.type.value, "source": item.source}; payload.update(extra or {})
            self.event_bus.emit(kind, "notifications", payload, correlation_id=item.id)
