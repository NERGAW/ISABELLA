"""Denial-first protocol validation and authenticated command gateway."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any

from Isabella.Skills.base import SkillResult
from .models import MessageType, NodeIdentity, ProtocolError, ProtocolMessage
from .version import PROTOCOL_VERSION, is_compatible


IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
CAPABILITY = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
FORBIDDEN_COMMAND_KEYS = frozenset({"python", "code", "shell", "command", "executable", "permissions", "confirmed", "confirmation_id", "risk_level"})


class ProtocolValidationError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}

    def to_error(self, request_id: str | None = None) -> ProtocolError:
        return ProtocolError(self.code, str(self), request_id, self.details)


def validate_identity(identity: NodeIdentity, available_capabilities: set[str]) -> None:
    if not IDENTIFIER.fullmatch(identity.node_id) or not identity.name.strip() or len(identity.name) > 100:
        raise ProtocolValidationError("INVALID_IDENTITY", "Node identity is invalid")
    if not is_compatible(identity.protocol_version):
        raise ProtocolValidationError("INCOMPATIBLE_VERSION", "Protocol version is not supported", {"supported": [PROTOCOL_VERSION]})
    if len(identity.capabilities) != len(set(identity.capabilities)):
        raise ProtocolValidationError("DUPLICATE_CAPABILITY", "Capabilities must be unique")
    for capability in identity.capabilities:
        if not CAPABILITY.fullmatch(capability) or capability not in available_capabilities:
            raise ProtocolValidationError("UNAVAILABLE_CAPABILITY", "Node announced an unavailable capability", {"capability": capability})


def validate_message(message: ProtocolMessage, *, available_capabilities: set[str] | None = None, authorized_events: set[str] | None = None) -> None:
    if not is_compatible(message.protocol_version):
        raise ProtocolValidationError("INCOMPATIBLE_VERSION", "Protocol version is not supported", {"supported": [PROTOCOL_VERSION]})
    if not all(IDENTIFIER.fullmatch(value) for value in (message.id, message.source, message.destination, message.correlation_id)):
        raise ProtocolValidationError("INVALID_ENVELOPE", "Envelope identifiers are invalid")
    try:
        timestamp = datetime.fromisoformat(message.timestamp)
    except ValueError as exc:
        raise ProtocolValidationError("INVALID_TIMESTAMP", "Timestamp is invalid") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ProtocolValidationError("INVALID_TIMESTAMP", "Timestamp must be timezone-aware")
    if not isinstance(message.payload, dict):
        raise ProtocolValidationError("INVALID_PAYLOAD", "Payload must be an object")
    _reject_embedded_images(message.payload)
    if message.type in {MessageType.HELLO, MessageType.WELCOME, MessageType.CAPABILITIES}:
        identity_data = message.payload.get("identity")
        if not isinstance(identity_data, dict):
            raise ProtocolValidationError("INVALID_IDENTITY", "Identity payload is required")
        identity = NodeIdentity.from_dict(identity_data)
        validate_identity(identity, available_capabilities or set())
        if identity.node_id != message.source:
            raise ProtocolValidationError("IDENTITY_MISMATCH", "Envelope source does not match node identity")
    elif message.type is MessageType.COMMAND_REQUEST:
        _validate_command_payload(message.payload)
    elif message.type is MessageType.HEARTBEAT:
        if set(message.payload) - {"status", "sequence"}:
            raise ProtocolValidationError("INVALID_HEARTBEAT", "Heartbeat contains unknown fields")
    elif message.type is MessageType.TELEMETRY:
        if set(message.payload) != {"capability", "metrics"} or not isinstance(message.payload.get("metrics"), dict):
            raise ProtocolValidationError("INVALID_TELEMETRY", "Telemetry requires capability and metrics")
        capability = message.payload["capability"]
        if not isinstance(capability, str) or not CAPABILITY.fullmatch(capability) or capability not in (available_capabilities or set()):
            raise ProtocolValidationError("UNAVAILABLE_CAPABILITY", "Telemetry capability is not authorized")
    elif message.type is MessageType.EVENT:
        if set(message.payload) != {"event", "data"} or message.payload.get("event") not in (authorized_events or set()) or not isinstance(message.payload.get("data"), dict):
            raise ProtocolValidationError("EVENT_NOT_AUTHORIZED", "Protocol event is not explicitly authorized")
    elif message.type is MessageType.ERROR:
        if not {"code", "message", "request_id", "details"} <= message.payload.keys() or not isinstance(message.payload.get("details"), dict):
            raise ProtocolValidationError("INVALID_ERROR", "Error payload is invalid")
    elif message.type is MessageType.STATUS:
        if not isinstance(message.payload.get("status"), str):
            raise ProtocolValidationError("INVALID_STATUS", "Status payload requires a status string")
    elif message.type is MessageType.COMMAND_RESULT:
        required = {"request_id", "success", "status", "message", "data", "error"}
        if set(message.payload) != required or not isinstance(message.payload["success"], bool) or not isinstance(message.payload["data"], dict):
            raise ProtocolValidationError("INVALID_COMMAND_RESULT", "Command result payload is invalid")


def dispatch_command(message: ProtocolMessage, *, authenticated: bool, registry, source_request_id: str | None = None, source_node: str | None = None) -> SkillResult:
    validate_message(message)
    if message.type is not MessageType.COMMAND_REQUEST:
        raise ProtocolValidationError("INVALID_MESSAGE_TYPE", "Only COMMAND_REQUEST can be dispatched")
    if not authenticated:
        raise ProtocolValidationError("AUTHENTICATION_REQUIRED", "Authenticated transport context is required")
    skill_id = message.payload["skill_id"]
    arguments = message.payload["arguments"]
    validation = registry.validate_arguments(skill_id, arguments)
    if validation:
        return validation
    # No confirmation flag/id is accepted from the protocol. Security is authoritative.
    return registry.execute(skill_id, arguments, source_request_id=source_request_id or message.correlation_id, source_node=source_node or message.source)


def _validate_command_payload(payload: dict[str, Any]) -> None:
    if set(payload) != {"skill_id", "arguments"}:
        forbidden = sorted(set(payload) & FORBIDDEN_COMMAND_KEYS)
        raise ProtocolValidationError("INVALID_COMMAND", "Command payload must contain only skill_id and arguments", {"forbidden_fields": forbidden[:10]})
    if not isinstance(payload["skill_id"], str) or not IDENTIFIER.fullmatch(payload["skill_id"]) or "." not in payload["skill_id"] or not isinstance(payload["arguments"], dict):
        raise ProtocolValidationError("INVALID_COMMAND", "Skill id or arguments are invalid")
    if any(key in FORBIDDEN_COMMAND_KEYS for key in payload["arguments"]):
        raise ProtocolValidationError("INVALID_COMMAND", "Command arguments contain forbidden control fields")


def _reject_embedded_images(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in {"image", "image_base64", "binary", "blob"}:
                raise ProtocolValidationError("EMBEDDED_BINARY_FORBIDDEN", "Binary/image data requires an explicit external strategy")
            _reject_embedded_images(item)
    elif isinstance(value, list):
        for item in value:
            _reject_embedded_images(item)
