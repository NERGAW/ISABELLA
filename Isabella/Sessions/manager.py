"""Thread-safe session routing without duplicating Brain or Memory."""

import threading
import uuid

from Isabella.Events import EventType
from .models import IsabellaSession, now_iso


class SessionManager:
    def __init__(self, *, context=None, event_bus=None, primary_node="primary.local") -> None:
        self.context = context
        self.event_bus = event_bus
        self.primary_node = primary_node
        self._sessions: dict[str, IsabellaSession] = {}
        self._node_sessions: dict[str, str] = {}
        self._lock = threading.RLock()

    def create(self, active_node: str, user_context=None) -> IsabellaSession:
        session = IsabellaSession(dict(user_context or {}), active_node, f"working:{uuid.uuid4().hex}")
        with self._lock:
            self._sessions[session.session_id] = session
            self._node_sessions[active_node] = session.session_id
        self._update_context(session)
        self._emit(EventType.SESSION_CREATED, session)
        return session

    def resolve(self, node_id: str, session_id: str | None = None) -> IsabellaSession:
        with self._lock:
            session = self._sessions.get(session_id or self._node_sessions.get(node_id, ""))
        if session is None:
            return self.create(node_id)
        if session_id and self._node_sessions.get(node_id) != session_id and session.active_node != node_id:
            raise PermissionError("Session was not handed off to this Node")
        return self.touch(session.session_id, node_id)

    def touch(self, session_id: str, active_node: str | None = None) -> IsabellaSession:
        with self._lock:
            session = self._sessions[session_id]
            session.last_activity = now_iso()
            if active_node:
                session.active_node = active_node
                self._node_sessions[active_node] = session_id
        self._update_context(session)
        return session

    def handoff_session(self, session_id: str, target_node: str) -> IsabellaSession:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                raise KeyError(f"Unknown session: {session_id}")
            previous = session.active_node
            session.active_node = target_node
            session.last_activity = now_iso()
            self._node_sessions[target_node] = session_id
        self._update_context(session)
        self._emit(EventType.SESSION_HANDOFF, session, {"previous_node": previous, "target_node": target_node})
        return session

    def get(self, session_id: str) -> IsabellaSession | None:
        with self._lock:
            return self._sessions.get(session_id)

    def diagnostics(self) -> dict[str, int]:
        with self._lock:
            return {"sessions": len(self._sessions), "active_nodes": len(self._node_sessions)}

    def latest_remote(self) -> IsabellaSession | None:
        with self._lock:
            items = [item for item in self._sessions.values() if item.active_node != self.primary_node]
            return items[-1] if items else None

    def _update_context(self, session: IsabellaSession) -> None:
        if self.context:
            metadata = dict(self.context.get("metadata", {}))
            metadata.update({"isabella_session_id": session.session_id, "active_node": session.active_node,
                             "working_memory_reference": session.working_memory_reference})
            self.context.set("metadata", metadata)

    def _emit(self, kind, session, extra=None) -> None:
        if self.event_bus:
            payload = {"session_id": session.session_id, "active_node": session.active_node}
            payload.update(extra or {})
            self.event_bus.emit(kind, "sessions", payload)
