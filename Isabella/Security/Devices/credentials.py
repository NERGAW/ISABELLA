"""Persistent public credential registry and replay cache."""

import json
from pathlib import Path
import threading
from time import time

from .models import DeviceRecord


class CredentialStore:
    def __init__(self, path: Path, replay_ttl_seconds: float = 60) -> None:
        self.path = path
        self.replay_ttl_seconds = replay_ttl_seconds
        self._lock = threading.RLock()
        self._records: dict[str, DeviceRecord] = {}
        self._seen: dict[str, float] = {}
        if path.exists():
            document = json.loads(path.read_text(encoding="utf-8"))
            self._records = {item["node_id"]: DeviceRecord.from_dict(item) for item in document.get("devices", [])}

    def get(self, node_id: str) -> DeviceRecord | None:
        with self._lock:
            return self._records.get(node_id)

    def list(self) -> list[DeviceRecord]:
        with self._lock:
            return list(self._records.values())

    def save(self, record: DeviceRecord) -> None:
        with self._lock:
            self._records[record.node_id] = record
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"devices": [item.to_dict() for item in self._records.values()]}, indent=2), encoding="utf-8")

    def accept_once(self, node_id: str, message_id: str, timestamp: float) -> bool:
        now = time()
        with self._lock:
            self._seen = {key: expiry for key, expiry in self._seen.items() if expiry > now}
            key = f"{node_id}:{message_id}"
            if abs(now - timestamp) > self.replay_ttl_seconds or key in self._seen:
                return False
            self._seen[key] = now + self.replay_ttl_seconds
            return True
