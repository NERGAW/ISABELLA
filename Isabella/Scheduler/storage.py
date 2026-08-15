"""Dedicated SQLite storage for scheduled tasks."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import threading

from .models import ScheduledTask


class SchedulerStorage:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS scheduled_tasks (id TEXT PRIMARY KEY, document TEXT NOT NULL)")
        self._db.commit()
        self._lock = threading.RLock()

    def save(self, task: ScheduledTask) -> None:
        document = json.dumps(task.to_dict(), ensure_ascii=False, sort_keys=True)
        with self._lock:
            self._db.execute("INSERT INTO scheduled_tasks(id, document) VALUES(?, ?) ON CONFLICT(id) DO UPDATE SET document=excluded.document", (task.id, document))
            self._db.commit()

    def get(self, task_id: str) -> ScheduledTask | None:
        with self._lock:
            row = self._db.execute("SELECT document FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone()
        return ScheduledTask.from_dict(json.loads(row[0])) if row else None

    def list(self) -> list[ScheduledTask]:
        with self._lock:
            rows = self._db.execute("SELECT document FROM scheduled_tasks ORDER BY id").fetchall()
        return [ScheduledTask.from_dict(json.loads(row[0])) for row in rows]

    def health_check(self) -> bool:
        with self._lock:
            return self._db.execute("SELECT 1").fetchone() == (1,)

    def close(self) -> None:
        with self._lock:
            self._db.close()

