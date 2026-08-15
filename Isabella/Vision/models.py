"""Small immutable models returned by Vision services."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class VisionSource(str, Enum):
    SCREEN = "SCREEN"
    ACTIVE_WINDOW = "ACTIVE_WINDOW"
    CAMERA = "CAMERA"


@dataclass(frozen=True)
class ImageCapture:
    source: VisionSource
    timestamp: str
    width: int
    height: int
    path: Path | None = None
    buffer: bytes | None = None
    active_window: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    temporary: bool = True


@dataclass(frozen=True)
class VisionResult:
    success: bool
    message: str
    capture: ImageCapture | None = None
    error_code: str | None = None
