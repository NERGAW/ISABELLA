"""Vision lifecycle and local temporary-file policy; no LLM logic."""

from __future__ import annotations

import json
import logging
from collections import deque
from pathlib import Path
from typing import Any

from Isabella.Core.config import ConfigurationError, PROJECT_ROOT
from .camera import CameraCapture
from .models import ImageCapture, VisionResult
from .screen import ScreenCapturer
from Isabella.Events import EventType


LOGGER = logging.getLogger("VISION")
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "vision.json"


def load_vision_config(path: Path | None = None) -> dict[str, Any]:
    target = path or DEFAULT_CONFIG_PATH
    try:
        config = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Invalid vision configuration: {target}") from exc
    required = {
        "enabled", "screen_capture_enabled", "camera_enabled", "camera_device",
        "continuous_capture", "max_image_width", "max_image_height", "temporary_images",
    }
    if not isinstance(config, dict) or required - config.keys():
        raise ConfigurationError("Vision configuration is missing required fields")
    if config["continuous_capture"] is not False:
        raise ConfigurationError("Continuous Vision capture is disabled in this phase")
    if int(config["max_image_width"]) < 1 or int(config["max_image_height"]) < 1:
        raise ConfigurationError("Vision image limits are invalid")
    return config


class VisionManager:
    def __init__(self, config: dict[str, Any], screen=None, camera=None, context=None, event_bus=None) -> None:
        self.config = config
        self.enabled = bool(config["enabled"])
        self.context = context
        self.event_bus = event_bus
        width, height = int(config["max_image_width"]), int(config["max_image_height"])
        self.screen = screen or ScreenCapturer(width, height)
        self.camera = camera or CameraCapture(config.get("camera_device"), width, height)
        self.status = "ONLINE" if self.enabled else "OFFLINE"
        self._temporary_paths: deque[Path] = deque()
        self.metrics = {"screen_capture_ms": self.screen.latencies_ms, "camera_capture_ms": self.camera.latencies_ms}

    @classmethod
    def from_config(cls, context=None, path: Path | None = None, event_bus=None) -> "VisionManager":
        return cls(load_vision_config(path), context=context, event_bus=event_bus)

    def capture_screen(self, screen_index: int = 0) -> VisionResult:
        self._emit_started("SCREEN")
        if not self.enabled or not self.config["screen_capture_enabled"]:
            return VisionResult(False, "Captura de tela está desativada.", error_code="SCREEN_DISABLED")
        try:
            capture = self.screen.capture_screen(screen_index, temporary=bool(self.config["temporary_images"]))
            return self._success(capture, "Captura de tela realizada.")
        except Exception as exc:
            LOGGER.warning("screen_capture_failed error=%s", type(exc).__name__)
            return self._failure("Não foi possível capturar a tela.", "SCREEN_CAPTURE_FAILED", "SCREEN")

    def capture_active_window(self) -> VisionResult:
        self._emit_started("ACTIVE_WINDOW")
        if not self.enabled or not self.config["screen_capture_enabled"]:
            return VisionResult(False, "Captura de janela está desativada.", error_code="SCREEN_DISABLED")
        try:
            capture = self.screen.capture_active_window(temporary=bool(self.config["temporary_images"]))
            return self._success(capture, "Captura da janela ativa realizada.")
        except Exception as exc:
            LOGGER.warning("active_window_capture_failed error=%s", type(exc).__name__)
            return self._failure("Não foi possível capturar a janela ativa.", "ACTIVE_WINDOW_CAPTURE_FAILED", "ACTIVE_WINDOW")

    def capture_camera(self) -> VisionResult:
        self._emit_started("CAMERA")
        if not self.enabled or not self.config["camera_enabled"]:
            return VisionResult(False, "Câmera está desativada.", error_code="CAMERA_DISABLED")
        try:
            capture = self.camera.capture_frame(temporary=bool(self.config["temporary_images"]))
            return self._success(capture, "Imagem da câmera capturada.")
        except Exception as exc:
            LOGGER.warning("camera_capture_failed error=%s", type(exc).__name__)
            return self._failure("Não foi possível capturar uma imagem da câmera.", "CAMERA_UNAVAILABLE", "CAMERA")
        finally:
            self.camera.close()

    def _success(self, capture: ImageCapture, message: str) -> VisionResult:
        if capture.temporary and capture.path:
            self._temporary_paths.append(capture.path)
            while len(self._temporary_paths) > 5:
                self._remove(self._temporary_paths.popleft())
        payload = {
            "source": capture.source.value, "timestamp": capture.timestamp,
            "active_window": capture.active_window, "width": capture.width, "height": capture.height,
        }
        if self.event_bus:
            self.event_bus.emit(EventType.VISION_CAPTURE_COMPLETED, "vision", payload)
        if self.context:
            self.context.update(
                last_vision_source=capture.source.value,
                last_capture_timestamp=capture.timestamp,
                last_capture_window=capture.active_window,
            )
        LOGGER.info("captured source=%s width=%d height=%d", capture.source.value, capture.width, capture.height)
        return VisionResult(True, message, capture)

    def _emit_started(self, source: str) -> None:
        if self.event_bus:
            self.event_bus.emit(EventType.VISION_CAPTURE_STARTED, "vision", {"source": source})

    def _failure(self, message: str, error_code: str, source: str) -> VisionResult:
        if self.event_bus:
            self.event_bus.emit(EventType.VISION_CAPTURE_FAILED, "vision", {"source": source, "error_code": error_code})
        return VisionResult(False, message, error_code=error_code)

    def health_check(self, check_camera: bool = False) -> dict[str, bool | None]:
        return {
            "screen": bool(self.enabled and self.config["screen_capture_enabled"] and self.screen.health_check()),
            "camera": self.camera.health_check() if check_camera and self.config["camera_enabled"] else None,
        }

    def cleanup(self, capture: ImageCapture) -> None:
        if capture.temporary and capture.path:
            self._remove(capture.path)
            try:
                self._temporary_paths.remove(capture.path)
            except ValueError:
                pass

    @staticmethod
    def _remove(path: Path) -> None:
        path.unlink(missing_ok=True)

    def shutdown(self) -> None:
        self.camera.close()
        while self._temporary_paths:
            self._remove(self._temporary_paths.popleft())
        self.status = "OFFLINE"
