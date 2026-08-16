"""Time-bounded, human-verified pairing and device authentication."""

import hashlib
import hmac
import json
from datetime import timedelta
from pathlib import Path
import secrets
import threading
from time import time
from typing import Any
import uuid

from Isabella.Core.config import ConfigurationError, PROJECT_ROOT
from Isabella.Events import EventType
from .credentials import CredentialStore
from .identity import verify_signature
from .models import DeviceRecord, PairingRequest, PairingState, utc_now

DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "device_security.json"


def load_device_security_config(path: Path | None = None) -> dict[str, Any]:
    target = path or DEFAULT_CONFIG_PATH
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Invalid device security configuration: {target}") from exc
    required = {"pairing_enabled_by_default", "pairing_window_seconds", "code_ttl_seconds", "credential_registry_file", "replay_window_seconds", "default_permissions"}
    if not isinstance(value, dict) or required - value.keys() or value["pairing_enabled_by_default"]:
        raise ConfigurationError("Device security config is invalid or pairing defaults to ON")
    return value


class DevicePairingManager:
    def __init__(self, config: dict[str, Any], *, event_bus=None, store: CredentialStore | None = None) -> None:
        self.config = config
        self.event_bus = event_bus
        path = Path(config["credential_registry_file"])
        path = path if path.is_absolute() else PROJECT_ROOT / path
        self.store = store or CredentialStore(path, float(config["replay_window_seconds"]))
        self._window_until = 0.0
        self._pending: dict[str, PairingRequest] = {}
        self._lock = threading.RLock()
        self.auth_failures = 0

    @classmethod
    def from_config(cls, path: Path | None = None, **kwargs) -> "DevicePairingManager":
        return cls(load_device_security_config(path), **kwargs)

    @property
    def pairing_open(self) -> bool:
        return time() < self._window_until

    def start_pairing(self) -> dict[str, Any]:
        self._window_until = time() + float(self.config["pairing_window_seconds"])
        self._emit(EventType.PAIRING_STARTED, {"expires_in_seconds": self.config["pairing_window_seconds"]})
        return {"pairing_open": True, "expires_in_seconds": self.config["pairing_window_seconds"]}

    def request_pairing(self, node_id: str, public_identity: str, requested_permissions=()) -> PairingRequest:
        if not self.pairing_open:
            self._fail("pairing_mode_off", node_id)
            raise PermissionError("Pairing mode is OFF")
        if self.store.get(node_id):
            self._fail("known_node", node_id)
            raise PermissionError("Known Node cannot start a new pairing")
        code = f"{secrets.randbelow(1_000_000):06d}"
        now = utc_now()
        request = PairingRequest(uuid.uuid4().hex, node_id, public_identity,
                                 hashlib.sha256(code.encode()).hexdigest(), code, now,
                                 now + timedelta(seconds=float(self.config["code_ttl_seconds"])),
                                 requested_permissions=tuple(requested_permissions))
        with self._lock:
            self._pending[request.pairing_id] = request
        self._emit(EventType.PAIRING_REQUESTED, {"pairing_id": request.pairing_id, "node_id": node_id})
        return request

    def verify_code(self, pairing_id: str, code: str) -> bool:
        request = self._pending.get(pairing_id)
        if not request or request.used or request.expired:
            self._fail("expired_or_used_code", getattr(request, "node_id", None))
            return False
        valid = hmac.compare_digest(request.code_digest, hashlib.sha256(code.encode()).hexdigest())
        if not valid:
            self._fail("wrong_code", request.node_id)
            return False
        request.state = PairingState.PENDING_APPROVAL
        return True

    def get_pairing_request(self, pairing_id: str) -> PairingRequest | None:
        request = self._pending.get(pairing_id)
        return request if request and not request.used and not request.expired else None

    def approve(self, pairing_id: str, permissions: tuple[str, ...] | None = None) -> DeviceRecord:
        request = self._pending.get(pairing_id)
        if not request or request.used or request.expired or request.state is not PairingState.PENDING_APPROVAL:
            self._fail("approval_not_ready", getattr(request, "node_id", None))
            raise PermissionError("Pairing is not ready for approval")
        allowed = set(self.config["default_permissions"])
        selected = set(permissions if permissions is not None else request.requested_permissions) & allowed
        record = DeviceRecord(request.node_id, request.public_identity, utc_now().isoformat(), PairingState.TRUSTED, tuple(sorted(selected)))
        request.used = True
        request.display_code = ""
        request.state = PairingState.TRUSTED
        self.store.save(record)
        self._emit(EventType.PAIRING_APPROVED, {"node_id": record.node_id})
        self._emit(EventType.NODE_TRUSTED, {"node_id": record.node_id})
        return record

    def authenticate(self, node_id: str, challenge: bytes, signature: str, message_id: str, timestamp: float) -> bool:
        record = self.store.get(node_id)
        valid = bool(record and record.trust_status is PairingState.TRUSTED and
                     self.store.accept_once(node_id, message_id, timestamp) and
                     verify_signature(record.public_identity, challenge, signature))
        self._emit(EventType.AUTH_SUCCESS if valid else EventType.AUTH_FAILED, {"node_id": node_id})
        if not valid:
            self.auth_failures += 1
        return valid

    def authorize(self, node_id: str, permission: str) -> bool:
        record = self.store.get(node_id)
        return bool(record and record.trust_status is PairingState.TRUSTED and permission in record.permissions)

    def revoke_node(self, node_id: str) -> DeviceRecord:
        record = self.store.get(node_id)
        if not record:
            raise KeyError(f"Unknown Node: {node_id}")
        record.trust_status = PairingState.REVOKED
        self.store.save(record)
        self._emit(EventType.NODE_REVOKED, {"node_id": node_id})
        return record

    def diagnostics(self) -> dict[str, int | bool]:
        records = self.store.list()
        return {"pairing_open": self.pairing_open, "trusted_nodes": sum(x.trust_status is PairingState.TRUSTED for x in records),
                "pending_pairing": sum(not x.used and not x.expired for x in self._pending.values()),
                "revoked_nodes": sum(x.trust_status is PairingState.REVOKED for x in records), "auth_failures": self.auth_failures}

    def _fail(self, reason: str, node_id: str | None) -> None:
        self.auth_failures += 1
        self._emit(EventType.PAIRING_FAILED, {"node_id": node_id, "reason": reason})

    def _emit(self, kind, payload) -> None:
        if self.event_bus:
            self.event_bus.emit(kind, "device_security", payload)
