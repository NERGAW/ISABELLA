"""Lightweight Node lifecycle, trust and Context coordination."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from Isabella.Core.config import ConfigurationError, PROJECT_ROOT
from Isabella.Events import EventType
from Isabella.Protocol import PROTOCOL_VERSION, is_compatible, validate_identity
from .models import Node, NodeStatus, NodeType, TrustState, now_iso
from .node import create_primary_node
from .registry import NodeRegistry


DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "nodes.json"


def load_nodes_config(path: Path | None = None) -> dict[str, Any]:
    target = path or DEFAULT_CONFIG_PATH
    try:
        config = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Invalid Nodes configuration: {target}") from exc
    required = {"enabled", "identity_file", "registry_file", "offline_after_seconds", "known_capabilities"}
    if not isinstance(config, dict) or required - config.keys():
        raise ConfigurationError("Nodes configuration is missing required fields")
    if not 5 <= float(config["offline_after_seconds"]) <= 86400 or not isinstance(config["known_capabilities"], list):
        raise ConfigurationError("Nodes limits or capabilities are invalid")
    return config


class NodeManager:
    def __init__(self, config: dict[str, Any], *, app=None, brain=None, controller=None, context=None, event_bus=None, registry: NodeRegistry | None = None, device_security=None) -> None:
        self.config = config
        self.enabled = bool(config["enabled"])
        self.app = app
        self.brain = brain
        self.controller = controller
        self.context = context or getattr(brain, "context", None)
        self.event_bus = event_bus
        self.device_security = device_security
        identity = Path(config["identity_file"])
        registry_path = Path(config["registry_file"])
        self.identity_path = identity if identity.is_absolute() else PROJECT_ROOT / identity
        self.registry = registry or NodeRegistry(registry_path if registry_path.is_absolute() else PROJECT_ROOT / registry_path)
        self.known_capabilities = set(config["known_capabilities"])
        self.primary_node_id: str | None = None

    @classmethod
    def from_config(cls, path: Path | None = None, **components) -> "NodeManager":
        return cls(load_nodes_config(path), **components)

    def start(self) -> bool:
        if not self.enabled:
            return True
        primary = create_primary_node(self.identity_path, app=self.app, brain=self.brain, controller=self.controller)
        self.known_capabilities.update(primary.capabilities)
        validate_identity(primary.protocol_identity(), self.known_capabilities)
        self.primary_node_id = primary.node_id
        self.registry.register(primary, replace_existing=True)
        self._emit(EventType.NODE_REGISTERED, primary)
        self._emit(EventType.NODE_ONLINE, primary)
        self._sync_context()
        self._sync_hud()
        return True

    def register(self, node: Node) -> Node:
        if not is_compatible(node.protocol_version):
            raise ValueError("Node protocol version is incompatible")
        validate_identity(node.protocol_identity(), self.known_capabilities)
        if node.node_type is NodeType.PRIMARY_PC and node.node_id != self.primary_node_id:
            raise ValueError("Only the local persistent Node may be PRIMARY_PC")
        if node.node_id != self.primary_node_id and node.trust is TrustState.TRUSTED:
            node.trust = TrustState.UNTRUSTED
        self._emit(EventType.NODE_DISCOVERED, node)
        now = now_iso()
        node.connected_at = node.connected_at or now
        node.last_seen = now
        node.status = NodeStatus.ONLINE
        self.registry.register(node)
        self._emit(EventType.NODE_REGISTERED, node)
        self._emit(EventType.NODE_ONLINE, node)
        self._sync_context()
        self._sync_hud()
        return node

    def register_local_home_gateway(self, node_id: str = "home.gateway") -> Node:
        existing = self.get(node_id)
        if existing:
            if existing.node_type is not NodeType.HOME or not existing.metadata.get("local_gateway"):
                raise ValueError("Home gateway identity conflicts with an existing Node")
            return self.heartbeat(node_id)
        node = Node(node_id, "ISABELLA Home Gateway", NodeType.HOME, NodeStatus.CONNECTING,
                    capabilities=("sensors",), metadata={"local_gateway": True}, trust=TrustState.UNTRUSTED)
        self.register(node)
        node.trust = TrustState.TRUSTED
        self.registry.save(node)
        return node

    def unregister(self, node_id: str) -> Node | None:
        if node_id == self.primary_node_id:
            raise ValueError("Primary Node cannot be unregistered while running")
        node = self.registry.unregister(node_id)
        self._sync_context()
        self._sync_hud()
        return node

    def get(self, node_id: str) -> Node | None:
        return self.registry.get(node_id)

    def list(self) -> list[Node]:
        return self.registry.list()

    def update_status(self, node_id: str, status: NodeStatus) -> Node:
        node = self._require(node_id)
        previous = node.status
        node.status = NodeStatus(status)
        self.registry.save(node)
        if node.status is NodeStatus.ONLINE and previous is not NodeStatus.ONLINE:
            self._emit(EventType.NODE_ONLINE, node)
        if node.status is NodeStatus.OFFLINE and previous is not NodeStatus.OFFLINE:
            self._emit(EventType.NODE_OFFLINE, node)
        self._sync_context()
        self._sync_hud()
        return node

    def update_capabilities(self, node_id: str, capabilities: tuple[str, ...] | list[str]) -> Node:
        node = self._require(node_id)
        updated = tuple(sorted(set(capabilities)))
        candidate = Node(node.node_id, node.name, node.node_type, node.status, node.protocol_version, updated, node.last_seen, node.connected_at, node.metadata, node.trust)
        validate_identity(candidate.protocol_identity(), self.known_capabilities)
        if updated != node.capabilities:
            node.capabilities = updated
            self.registry.save(node)
            self._emit(EventType.NODE_CAPABILITIES_CHANGED, node)
            self._sync_context()
        return node

    def heartbeat(self, node_id: str) -> Node:
        node = self._require(node_id)
        if node.trust is TrustState.REVOKED:
            raise PermissionError("Revoked Node heartbeat is rejected")
        was_online = node.status is NodeStatus.ONLINE
        node.last_seen = now_iso()
        node.status = NodeStatus.ONLINE
        self.registry.save(node)
        if not was_online:
            self._emit(EventType.NODE_ONLINE, node)
        self._sync_context()
        self._sync_hud()
        return node

    def mark_offline(self, now: datetime | None = None) -> list[Node]:
        current = now or datetime.now(timezone.utc)
        threshold = float(self.config["offline_after_seconds"])
        changed = []
        for node in self.list():
            if node.status is not NodeStatus.ONLINE or not node.last_seen or node.node_id == self.primary_node_id:
                continue
            seen = datetime.fromisoformat(node.last_seen)
            if (current - seen).total_seconds() >= threshold:
                node.status = NodeStatus.OFFLINE
                self.registry.save(node)
                self._emit(EventType.NODE_OFFLINE, node)
                changed.append(node)
        if changed:
            self._sync_context()
            self._sync_hud()
        return changed

    def revoke(self, node_id: str) -> Node:
        if node_id == self.primary_node_id:
            raise ValueError("Primary Node trust cannot be revoked locally")
        node = self._require(node_id)
        node.trust = TrustState.REVOKED
        node.status = NodeStatus.DISCONNECTED
        if self.device_security and self.device_security.store.get(node_id):
            self.device_security.revoke_node(node_id)
        self.registry.save(node)
        self._emit(EventType.NODE_REVOKED, node)
        self._sync_context()
        self._sync_hud()
        return node

    def trust(self, node_id: str) -> Node:
        node = self._require(node_id)
        record = self.device_security.store.get(node_id) if self.device_security else None
        if not record or record.trust_status.value != "TRUSTED":
            raise PermissionError("Cryptographic device approval is required")
        node.trust = TrustState.TRUSTED
        self.registry.save(node)
        self._emit(EventType.NODE_TRUSTED, node)
        self._sync_hud()
        return node

    def primary(self) -> Node | None:
        return self.get(self.primary_node_id) if self.primary_node_id else None

    def hello(self):
        primary = self.primary()
        if primary is None:
            raise RuntimeError("Primary Node is not registered")
        return primary.hello("local.primary")

    def diagnostics(self) -> dict[str, Any]:
        nodes = self.list()
        capabilities = sorted({capability for node in nodes if node.status is NodeStatus.ONLINE for capability in node.capabilities})
        details = {"enabled": self.enabled, "nodes_total": len(nodes), "online": sum(node.status is NodeStatus.ONLINE for node in nodes), "offline": sum(node.status in {NodeStatus.OFFLINE, NodeStatus.DISCONNECTED} for node in nodes), "capabilities": capabilities, "primary_node": self.primary_node_id}
        if self.device_security:
            details.update(self.device_security.diagnostics())
        return details

    def shutdown(self) -> bool:
        primary = self.primary()
        if primary:
            primary.status = NodeStatus.DISCONNECTED
            self.registry.save(primary)
        self._sync_context()
        self._sync_hud()
        return True

    def _sync_context(self) -> None:
        if not self.context:
            return
        active = tuple(sorted(node.node_id for node in self.list() if node.status is NodeStatus.ONLINE))
        capabilities = tuple(sorted({item for node in self.list() if node.status is NodeStatus.ONLINE for item in node.capabilities}))
        self.context.update(active_nodes=active, primary_node=self.primary_node_id, available_capabilities=capabilities)

    def _sync_hud(self) -> None:
        if self.controller:
            online = sum(node.status is NodeStatus.ONLINE for node in self.list())
            self.controller.update_subsystem("NODES", f"{online} ONLINE")
            if self.device_security:
                devices = self.device_security.diagnostics()
                self.controller.update_subsystem("DEVICES", f"Trusted: {devices['trusted_nodes']} | Pending: {devices['pending_pairing']}")

    def _require(self, node_id: str) -> Node:
        node = self.get(node_id)
        if node is None:
            raise KeyError(f"Unknown Node: {node_id}")
        return node

    def _emit(self, event_type, node: Node) -> None:
        if self.event_bus:
            self.event_bus.emit(event_type, "nodes", {"node_id": node.node_id, "node_type": node.node_type.value, "status": node.status.value, "trust": node.trust.value})
