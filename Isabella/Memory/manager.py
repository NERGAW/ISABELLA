"""Public memory service isolated from SQLite details."""

from __future__ import annotations

import json
import logging
from collections import deque
from pathlib import Path
from time import perf_counter
from datetime import datetime, timezone
from typing import Any

from Isabella.Core.config import ConfigurationError, PROJECT_ROOT
from .database import MemoryDatabase
from .models import MemoryRecord, MemoryType, WorkingMessage
from .retrieval import contains_secret, keywords
from Isabella.Events import EventType


LOGGER = logging.getLogger("MEMORY")
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "memory.json"


class MemoryError(RuntimeError):
    pass


class SecretMemoryError(MemoryError):
    pass


def load_memory_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or DEFAULT_CONFIG_PATH
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Invalid memory configuration: {config_path}") from exc
    required = {"enabled", "database_path", "working_memory_max_messages", "max_retrieval_results"}
    if not isinstance(config, dict) or required - config.keys():
        raise ConfigurationError("Memory configuration is missing required fields")
    if int(config["working_memory_max_messages"]) < 1 or not 1 <= int(config["max_retrieval_results"]) <= 20:
        raise ConfigurationError("Memory limits are invalid")
    return config


class MemoryManager:
    def __init__(self, config: dict[str, Any], database: MemoryDatabase | None = None, event_bus=None) -> None:
        self.config = config
        self.enabled = bool(config.get("enabled", True))
        self.status = "OFFLINE" if not self.enabled else "STARTING"
        self.database = database
        self.event_bus = event_bus
        self.knowledge = None
        self.working_memory: deque[WorkingMessage] = deque(
            maxlen=int(config["working_memory_max_messages"])
        )
        self.max_results = int(config.get("max_retrieval_results", 5))
        self.metrics = {"write_ms": deque(maxlen=200), "recall_ms": deque(maxlen=200), "retrieval_ms": deque(maxlen=200)}
        self.last_write_at: str | None = None
        self.last_read_at: str | None = None
        if self.enabled and self.database is None:
            path = Path(str(config["database_path"]))
            self.database = MemoryDatabase(path if path.is_absolute() else PROJECT_ROOT / path)
        self.status = "ONLINE" if self.database else "OFFLINE"

    @classmethod
    def from_config(cls, path: Path | None = None, event_bus=None) -> "MemoryManager":
        config = load_memory_config(path)
        try:
            return cls(config, event_bus=event_bus)
        except Exception as exc:
            LOGGER.error("initialization_failed error=%s", type(exc).__name__)
            manager = cls.__new__(cls)
            manager.config = config
            manager.enabled = bool(config.get("enabled", True))
            manager.status = "ERROR"
            manager.database = None
            manager.event_bus = event_bus
            manager.working_memory = deque(maxlen=int(config["working_memory_max_messages"]))
            manager.max_results = int(config.get("max_retrieval_results", 5))
            manager.metrics = {"write_ms": deque(maxlen=200), "recall_ms": deque(maxlen=200), "retrieval_ms": deque(maxlen=200)}
            manager.last_write_at = None
            manager.last_read_at = None
            return manager

    @staticmethod
    def _type(value: MemoryType | str) -> MemoryType:
        try:
            return value if isinstance(value, MemoryType) else MemoryType(value)
        except ValueError as exc:
            raise MemoryError(f"Invalid memory type: {value}") from exc

    def remember(
        self, memory_type: MemoryType | str, key: str, value: str, *,
        source: str = "user_explicit", confidence: float = 1.0,
        tags: tuple[str, ...] = (), metadata: dict[str, Any] | None = None,
    ) -> MemoryRecord:
        if contains_secret(f"{key} {value}"):
            raise SecretMemoryError("Memory não é um cofre de credenciais.")
        if not self.database:
            raise MemoryError("Memory database is unavailable")
        normalized_type = self._type(memory_type)
        if normalized_type is MemoryType.WORKING_MEMORY:
            raise MemoryError("Working memory is not persisted")
        if not key.strip() or not value.strip() or not 0.0 <= confidence <= 1.0:
            raise MemoryError("Invalid memory data")
        started = perf_counter()
        try:
            record = self.database.upsert(normalized_type, key.strip(), value.strip(), source, confidence, tags, metadata or {})
        except Exception as exc:
            self._database_failed(exc)
            raise MemoryError("Memory database is unavailable") from exc
        self.metrics["write_ms"].append((perf_counter() - started) * 1000)
        self.last_write_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        LOGGER.info("created id=%s type=%s", record.id, record.type.value)
        if self.event_bus:
            self.event_bus.emit(EventType.MEMORY_CREATED, "memory", {"id": record.id, "type": record.type.value, "key": record.key})
        if self.knowledge and source == "user_explicit":
            self.knowledge.ingest_memory(record)
        return record

    def recall(self, key: str, memory_type: MemoryType | str | None = None) -> list[MemoryRecord]:
        if not self.database:
            return []
        started = perf_counter()
        kind = self._type(memory_type) if memory_type else None
        try:
            records = self.database.get(key, kind)[: self.max_results]
        except Exception as exc:
            self._database_failed(exc)
            return []
        self.metrics["recall_ms"].append((perf_counter() - started) * 1000)
        self.last_read_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        LOGGER.info("recalled count=%d", len(records))
        if self.event_bus:
            self.event_bus.emit(EventType.MEMORY_RECALLED, "memory", {"count": len(records), "key": key})
        return records

    def forget(self, key: str, memory_type: MemoryType | str | None = None) -> int:
        if not self.database:
            return 0
        kind = self._type(memory_type) if memory_type else None
        try:
            ids = self.database.forget(key, kind)
        except Exception as exc:
            self._database_failed(exc)
            return 0
        for memory_id in ids:
            LOGGER.info("forgotten id=%s", memory_id)
            if self.event_bus:
                self.event_bus.emit(EventType.MEMORY_REMOVED, "memory", {"id": memory_id, "key": key})
        if ids:
            self.last_write_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        return len(ids)

    def search(self, query: str, limit: int | None = None) -> list[MemoryRecord]:
        if not self.database:
            return []
        started = perf_counter()
        try:
            records = self.database.search(keywords(query), min(limit or self.max_results, self.max_results))
        except Exception as exc:
            self._database_failed(exc)
            return []
        self.metrics["retrieval_ms"].append((perf_counter() - started) * 1000)
        self.last_read_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        LOGGER.info("recalled count=%d", len(records))
        if self.event_bus:
            self.event_bus.emit(EventType.MEMORY_RECALLED, "memory", {"count": len(records), "query_terms": len(keywords(query))})
        return records

    def list_memories(self, memory_type: MemoryType | str | None = None) -> list[MemoryRecord]:
        if not self.database:
            return []
        try:
            records = self.database.list(self._type(memory_type) if memory_type else None)
            self.last_read_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
            return records
        except Exception as exc:
            self._database_failed(exc)
            return []

    def add_working_message(self, role: str, text: str) -> None:
        if text.strip():
            self.working_memory.append(WorkingMessage(role, text.strip()))

    def clear_working_memory(self) -> None:
        self.working_memory.clear()

    def relevant_context(self, query: str) -> str:
        persistent = self.search(query)
        pieces = ["Memórias persistentes relevantes:"]
        pieces.extend(f"- {record.key}: {record.value}" for record in persistent)
        recent = list(self.working_memory)[-8:]
        if recent:
            pieces.append("Contexto recente da sessão:")
            pieces.extend(f"- {message.role}: {message.text}" for message in recent)
        return "\n".join(pieces) if len(pieces) > 1 else ""

    def close(self) -> None:
        if self.database:
            try:
                self.database.close()
            except Exception as exc:
                self._database_failed(exc)
        self.status = "OFFLINE"

    def _database_failed(self, exc: Exception) -> None:
        self.status = "ERROR"
        LOGGER.error("database_failed error=%s", type(exc).__name__)
