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


@dataclass(frozen=True)
class ScreenAnalysis:
    summary: str
    visible_text: tuple[str, ...] = ()
    applications: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    ui_elements: tuple[str, ...] = ()
    confidence: float | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"summary": self.summary}
        for name in ("visible_text", "applications", "errors", "ui_elements"):
            value = getattr(self, name)
            if value:
                result[name] = list(value)
        if self.confidence is not None:
            result["confidence"] = self.confidence
        return result

    def to_message(self) -> str:
        parts = [self.summary]
        if self.errors:
            parts.append("Erro visível: " + "; ".join(self.errors[:2]))
        if self.confidence is not None and self.confidence < 0.6:
            parts.append("A análise tem baixa confiança; detalhes pequenos podem não estar legíveis.")
        return " ".join(part for part in parts if part).strip()


@dataclass(frozen=True)
class VisionAnalysisResult:
    success: bool
    message: str
    analysis: ScreenAnalysis | None = None
    capture: ImageCapture | None = None
    timings_ms: dict[str, float] = field(default_factory=dict)
    error_code: str | None = None
    reused: bool = False
