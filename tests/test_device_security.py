from datetime import timedelta
from pathlib import Path
from time import time

import pytest

from Isabella.Security.Devices import CredentialStore, DeviceIdentity, DevicePairingManager, PairingState


class Bus:
    def __init__(self): self.events = []
    def emit(self, kind, source, payload=None):
        self.events.append(kind.value if hasattr(kind, "value") else kind)


def config(tmp_path):
    return {"pairing_enabled_by_default": False, "pairing_window_seconds": 30,
            "code_ttl_seconds": 10, "credential_registry_file": str(tmp_path / "devices.json"),
            "replay_window_seconds": 60,
            "default_permissions": ["send_commands", "receive_notifications", "send_telemetry"]}


def pair(tmp_path, node_id="mobile.test", permissions=("send_commands",)):
    bus = Bus()
    manager = DevicePairingManager(config(tmp_path), event_bus=bus)
    identity = DeviceIdentity.load_or_create(node_id, tmp_path / f"{node_id}.pem")
    manager.start_pairing()
    request = manager.request_pairing(node_id, identity.public_identity, permissions)
    assert manager.verify_code(request.pairing_id, request.display_code)
    record = manager.approve(request.pairing_id)
    return manager, identity, request, record, bus


def test_valid_pairing_authentication_and_permissions(tmp_path):
    manager, identity, request, record, bus = pair(tmp_path)
    assert record.trust_status is PairingState.TRUSTED
    assert manager.authorize(record.node_id, "send_commands")
    proof = b"challenge"
    assert manager.authenticate(record.node_id, proof, identity.sign(proof), "message-1", time())
    assert {"pairing.started", "pairing.requested", "pairing.approved", "node.trusted", "auth.success"} <= set(bus.events)
    restored = CredentialStore(Path(config(tmp_path)["credential_registry_file"]))
    assert restored.get(record.node_id).public_identity == identity.public_identity


def test_wrong_expired_and_reused_code_fail(tmp_path):
    manager = DevicePairingManager(config(tmp_path))
    identity = DeviceIdentity.load_or_create("mobile.bad", tmp_path / "bad.pem")
    manager.start_pairing()
    wrong = manager.request_pairing("mobile.bad", identity.public_identity)
    assert not manager.verify_code(wrong.pairing_id, "000000")
    expired = manager.request_pairing("mobile.expired", identity.public_identity)
    expired.expires_at = expired.created_at - timedelta(seconds=1)
    assert not manager.verify_code(expired.pairing_id, expired.display_code)
    valid = manager.request_pairing("mobile.once", identity.public_identity)
    assert manager.verify_code(valid.pairing_id, valid.display_code)
    manager.approve(valid.pairing_id)
    assert not manager.verify_code(valid.pairing_id, valid.display_code)


def test_pairing_off_unknown_mismatch_replay_and_revocation(tmp_path):
    manager = DevicePairingManager(config(tmp_path))
    identity = DeviceIdentity.load_or_create("mobile.test", tmp_path / "device.pem")
    with pytest.raises(PermissionError, match="OFF"):
        manager.request_pairing("mobile.test", identity.public_identity)
    manager, identity, _, record, _ = pair(tmp_path)
    attacker = DeviceIdentity.load_or_create("attacker", tmp_path / "attacker.pem")
    proof = b"proof"
    assert not manager.authenticate(record.node_id, proof, attacker.sign(proof), "bad", time())
    assert manager.authenticate(record.node_id, proof, identity.sign(proof), "once", time())
    assert not manager.authenticate(record.node_id, proof, identity.sign(proof), "once", time())
    manager.revoke_node(record.node_id)
    assert not manager.authenticate(record.node_id, proof, identity.sign(proof), "after-revoke", time())
    assert not manager.authorize(record.node_id, "send_commands")


def test_attack_cannot_claim_permissions_trust_or_other_node_id(tmp_path):
    manager, identity, _, record, _ = pair(tmp_path, permissions=("send_commands", "admin", "security_config"))
    assert record.permissions == ("send_commands",)
    assert not manager.authorize(record.node_id, "admin")
    forged = DeviceIdentity.load_or_create("forged", tmp_path / "forged.pem")
    proof = b"immutable-source"
    assert not manager.authenticate(record.node_id, proof, forged.sign(proof), "forge-1", time())
    assert manager.store.get(record.node_id).trust_status is PairingState.TRUSTED
    assert manager.diagnostics()["auth_failures"] >= 1
