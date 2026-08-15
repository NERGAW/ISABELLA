"""Dedicated SQLite persistence for automation rules."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import threading

from .models import Automation


class AutomationStorage:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.execute("CREATE TABLE IF NOT EXISTS automations (id TEXT PRIMARY KEY, document TEXT NOT NULL)")
        self._connection.commit()
        self._lock = threading.RLock()

    def save(self, automation: Automation) -> None:
        document = json.dumps(automation.to_dict(), ensure_ascii=False, sort_keys=True)
        with self._lock:
            self._connection.execute(
                "INSERT INTO automations(id, document) VALUES(?, ?) ON CONFLICT(id) DO UPDATE SET document=excluded.document",
                (automation.id, document),
            )
            self._connection.commit()

    def get(self, automation_id: str) -> Automation | None:
        with self._lock:
            row = self._connection.execute("SELECT document FROM automations WHERE id=?", (automation_id,)).fetchone()
        return Automation.from_dict(json.loads(row[0])) if row else None

    def list(self) -> list[Automation]:
        with self._lock:
            rows = self._connection.execute("SELECT document FROM automations ORDER BY id").fetchall()
        return [Automation.from_dict(json.loads(row[0])) for row in rows]

    def delete(self, automation_id: str) -> bool:
        with self._lock:
            cursor = self._connection.execute("DELETE FROM automations WHERE id=?", (automation_id,))
            self._connection.commit()
        return cursor.rowcount > 0

    def health_check(self) -> bool:
        with self._lock:
            return self._connection.execute("SELECT 1").fetchone() == (1,)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

