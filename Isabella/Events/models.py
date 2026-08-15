"""Immutable event envelope with correlation and priority."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any
import uuid
from contextvars import ContextVar, Token


_CORRELATION_ID: ContextVar[str | None] = ContextVar("isabella_correlation_id", default=None)


def get_correlation_id() -> str | None:
    return _CORRELATION_ID.get()


def set_correlation_id(value: str | None) -> Token:
    return _CORRELATION_ID.set(value)


def reset_correlation_id(token: Token) -> None:
    _CORRELATION_ID.reset(token)


class EventPriority(IntEnum):
    HIGH = 0
    NORMAL = 10
    LOW = 20


@dataclass(frozen=True)
class Event:
    type: str
    source: str
    payload: dict[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None
    priority: EventPriority = EventPriority.NORMAL
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    )
