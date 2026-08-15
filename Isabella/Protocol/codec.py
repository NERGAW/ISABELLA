"""Bounded JSON codec and v1 handshake helpers."""

from __future__ import annotations

import json
from typing import Any

from .models import MessageType, NodeIdentity, ProtocolError, ProtocolMessage
from .validation import ProtocolValidationError, validate_identity, validate_message
from .version import PROTOCOL_VERSION, is_compatible


MAX_MESSAGE_BYTES = 64 * 1024
MAX_NESTING_DEPTH = 12
ENVELOPE_FIELDS = frozenset({"id", "protocol_version", "type", "source", "destination", "timestamp", "correlation_id", "payload"})


def encode(message: ProtocolMessage, *, max_bytes: int = MAX_MESSAGE_BYTES, available_capabilities: set[str] | None = None, authorized_events: set[str] | None = None) -> bytes:
    validate_message(message, available_capabilities=available_capabilities, authorized_events=authorized_events)
    _validate_json_value(message.to_dict(), 0)
    try:
        encoded = json.dumps(message.to_dict(), ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProtocolValidationError("INVALID_JSON_VALUE", "Message contains a non-JSON value") from exc
    if len(encoded) > max_bytes:
        raise ProtocolValidationError("MESSAGE_TOO_LARGE", "Encoded message exceeds the configured limit", {"max_bytes": max_bytes})
    return encoded


def decode(data: bytes | str, *, max_bytes: int = MAX_MESSAGE_BYTES, available_capabilities: set[str] | None = None, authorized_events: set[str] | None = None) -> ProtocolMessage:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if not isinstance(raw, bytes):
        raise ProtocolValidationError("INVALID_ENCODING", "Message must be UTF-8 bytes or text")
    if len(raw) > max_bytes:
        raise ProtocolValidationError("MESSAGE_TOO_LARGE", "Incoming message exceeds the configured limit", {"max_bytes": max_bytes})
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolValidationError("INVALID_JSON", "Message is not valid UTF-8 JSON") from exc
    if not isinstance(document, dict) or set(document) != ENVELOPE_FIELDS:
        raise ProtocolValidationError("INVALID_ENVELOPE", "Envelope fields are missing or unknown")
    _validate_json_value(document, 0)
    try:
        message = ProtocolMessage.from_dict(document)
    except (KeyError, TypeError, ValueError) as exc:
        raise ProtocolValidationError("INVALID_ENVELOPE", "Envelope values are invalid") from exc
    validate_message(message, available_capabilities=available_capabilities, authorized_events=authorized_events)
    return message


def validate(message: ProtocolMessage, **kwargs) -> bool:
    validate_message(message, **kwargs)
    _validate_json_value(message.to_dict(), 0)
    return True


def negotiate_hello(message: ProtocolMessage, primary: NodeIdentity, *, primary_capabilities: set[str], peer_capabilities: set[str], heartbeat_seconds: int = 15) -> ProtocolMessage:
    if message.type is not MessageType.HELLO:
        return error_message(message, ProtocolError("EXPECTED_HELLO", "First message must be HELLO", message.id))
    if not is_compatible(message.protocol_version):
        return error_message(message, ProtocolError("INCOMPATIBLE_VERSION", "Protocol version is not supported", message.id, {"supported": [PROTOCOL_VERSION]}))
    try:
        validate_identity(primary, primary_capabilities)
        identity_data = message.payload.get("identity")
        if not isinstance(identity_data, dict):
            raise ProtocolValidationError("INVALID_IDENTITY", "HELLO identity is required")
        validate_identity(NodeIdentity.from_dict(identity_data), peer_capabilities)
    except (KeyError, TypeError, ValueError, ProtocolValidationError) as exc:
        error = exc if isinstance(exc, ProtocolValidationError) else ProtocolValidationError("INVALID_IDENTITY", "HELLO identity is invalid")
        return error_message(message, error.to_error(message.id))
    if not 5 <= heartbeat_seconds <= 30:
        return error_message(message, ProtocolError("INVALID_HEARTBEAT_INTERVAL", "Heartbeat must be between 5 and 30 seconds", message.id))
    return ProtocolMessage(MessageType.WELCOME, primary.node_id, message.source, {"identity": primary.to_dict(), "accepted_version": PROTOCOL_VERSION, "heartbeat_seconds": heartbeat_seconds}, correlation_id=message.correlation_id)


def error_message(request: ProtocolMessage, error: ProtocolError) -> ProtocolMessage:
    details = dict(list(error.details.items())[:10])
    payload = ProtocolError(error.code[:64], error.message[:500], error.request_id, details).to_dict()
    return ProtocolMessage(MessageType.ERROR, request.destination, request.source, payload, correlation_id=request.correlation_id)


def _validate_json_value(value: Any, depth: int) -> None:
    if depth > MAX_NESTING_DEPTH:
        raise ProtocolValidationError("PAYLOAD_TOO_DEEP", "Message nesting exceeds the configured limit")
    if value is None or isinstance(value, (str, int, float, bool)):
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item, depth + 1)
        return
    if isinstance(value, dict):
        if any(not isinstance(key, str) or len(key) > 128 for key in value):
            raise ProtocolValidationError("INVALID_JSON_VALUE", "Object keys must be short strings")
        for item in value.values():
            _validate_json_value(item, depth + 1)
        return
    raise ProtocolValidationError("INVALID_JSON_VALUE", "Message contains a non-JSON value")

