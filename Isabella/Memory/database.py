"""Thread-safe parameterized SQLite storage for persistent memories."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .models import MemoryRecord, MemoryType


SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0 CHECK(confidence BETWEEN 0.0 AND 1.0),
    tags TEXT NOT NULL DEFAULT '[]',
    metadata TEXT NOT NULL DEFAULT '{}',
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
    UNIQUE(type, key)
);
CREATE INDEX IF NOT EXISTS idx_memories_active_type ON memories(active, type);
CREATE INDEX IF NOT EXISTS idx_memories_key ON memories(key);
"""


class MemoryDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.executescript(SCHEMA)
            self._connection.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    @staticmethod
    def _record(row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            id=row["id"], type=MemoryType(row["type"]), key=row["key"], value=row["value"],
            source=row["source"], created_at=row["created_at"], updated_at=row["updated_at"],
            confidence=float(row["confidence"]), tags=tuple(json.loads(row["tags"])),
            metadata=json.loads(row["metadata"]), active=bool(row["active"]),
        )

    def upsert(
        self, memory_type: MemoryType, key: str, value: str, source: str,
        confidence: float, tags: tuple[str, ...], metadata: dict,
    ) -> MemoryRecord:
        now = self._now()
        with self._lock:
            self._connection.execute(
                """INSERT INTO memories
                (type, key, value, source, created_at, updated_at, confidence, tags, metadata, active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(type, key) DO UPDATE SET
                value=excluded.value, source=excluded.source, updated_at=excluded.updated_at,
                confidence=excluded.confidence, tags=excluded.tags, metadata=excluded.metadata, active=1""",
                (memory_type.value, key, value, source, now, now, confidence,
                 json.dumps(tags, ensure_ascii=False), json.dumps(metadata, ensure_ascii=False)),
            )
            self._connection.commit()
            row = self._connection.execute(
                "SELECT * FROM memories WHERE type = ? AND key = ?", (memory_type.value, key)
            ).fetchone()
        return self._record(row)

    def get(self, key: str, memory_type: MemoryType | None = None) -> list[MemoryRecord]:
        sql = "SELECT * FROM memories WHERE active = 1 AND key = ?"
        params: list[object] = [key]
        if memory_type:
            sql += " AND type = ?"
            params.append(memory_type.value)
        sql += " ORDER BY updated_at DESC"
        with self._lock:
            rows = self._connection.execute(sql, params).fetchall()
        return [self._record(row) for row in rows]

    def search(self, terms: tuple[str, ...], limit: int = 5) -> list[MemoryRecord]:
        if not terms:
            return []
        clauses = []
        params: list[object] = []
        for term in terms:
            clauses.append("(key LIKE ? ESCAPE '\\' OR value LIKE ? ESCAPE '\\' OR tags LIKE ? ESCAPE '\\')")
            escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.extend([f"%{escaped}%"] * 3)
        sql = "SELECT * FROM memories WHERE active = 1 AND (" + " OR ".join(clauses) + ") ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._connection.execute(sql, params).fetchall()
        return [self._record(row) for row in rows]

    def list(self, memory_type: MemoryType | None = None, limit: int = 100) -> list[MemoryRecord]:
        sql = "SELECT * FROM memories WHERE active = 1"
        params: list[object] = []
        if memory_type:
            sql += " AND type = ?"
            params.append(memory_type.value)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._connection.execute(sql, params).fetchall()
        return [self._record(row) for row in rows]

    def forget(self, key: str, memory_type: MemoryType | None = None) -> list[int]:
        sql = "SELECT id FROM memories WHERE active = 1 AND key = ?"
        params: list[object] = [key]
        if memory_type:
            sql += " AND type = ?"
            params.append(memory_type.value)
        with self._lock:
            ids = [row["id"] for row in self._connection.execute(sql, params).fetchall()]
            if ids:
                updated_at = self._now()
                self._connection.executemany(
                    "UPDATE memories SET active = 0, updated_at = ? WHERE id = ?",
                    [(updated_at, memory_id) for memory_id in ids],
                )
                self._connection.commit()
        return ids

    def close(self) -> None:
        with self._lock:
            self._connection.close()
