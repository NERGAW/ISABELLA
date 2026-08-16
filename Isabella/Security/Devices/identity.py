"""Ed25519 device identities; private keys never leave their owner."""

import base64
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.exceptions import InvalidSignature


@dataclass(frozen=True)
class DeviceIdentity:
    node_id: str
    private_key: Ed25519PrivateKey

    @classmethod
    def load_or_create(cls, node_id: str, path: Path) -> "DeviceIdentity":
        if path.exists():
            key = serialization.load_pem_private_key(path.read_bytes(), password=None)
            if not isinstance(key, Ed25519PrivateKey):
                raise ValueError("Device credential is not Ed25519")
            return cls(node_id, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        key = Ed25519PrivateKey.generate()
        path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return cls(node_id, key)

    @property
    def public_identity(self) -> str:
        raw = self.private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        return base64.urlsafe_b64encode(raw).decode("ascii")

    def sign(self, challenge: bytes) -> str:
        return base64.urlsafe_b64encode(self.private_key.sign(challenge)).decode("ascii")


def verify_signature(public_identity: str, challenge: bytes, signature: str) -> bool:
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.urlsafe_b64decode(public_identity))
        key.verify(base64.urlsafe_b64decode(signature), challenge)
        return True
    except (ValueError, TypeError, InvalidSignature):
        return False
