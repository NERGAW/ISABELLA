"""Small thread-safe persistent registry of known Nodes."""

from __future__ import annotations

import json
from pathlib import Path
import threading

from .models import Node


class NodeRegistry:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._nodes: dict[str, Node] = {}
        self._lock = threading.RLock()
        if path and path.exists():
            document = json.loads(path.read_text(encoding="utf-8"))
            self._nodes = {item["node_id"]: Node.from_dict(item) for item in document.get("nodes", [])}

    def register(self, node: Node, *, replace_existing: bool = False) -> None:
        with self._lock:
            if node.node_id in self._nodes and not replace_existing:
                raise ValueError(f"Node already registered: {node.node_id}")
            self._nodes[node.node_id] = node
            self._persist()

    def unregister(self, node_id: str) -> Node | None:
        with self._lock:
            node = self._nodes.pop(node_id, None)
            self._persist()
            return node

    def get(self, node_id: str) -> Node | None:
        with self._lock:
            return self._nodes.get(node_id)

    def list(self) -> list[Node]:
        with self._lock:
            return list(self._nodes.values())

    def save(self, node: Node) -> None:
        with self._lock:
            if node.node_id not in self._nodes:
                raise KeyError(f"Unknown Node: {node.node_id}")
            self._nodes[node.node_id] = node
            self._persist()

    def _persist(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"nodes": [item.to_dict() for item in self._nodes.values()]}, ensure_ascii=False, indent=2), encoding="utf-8")

