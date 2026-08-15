"""Vision lifecycle and local temporary-file policy; no LLM logic."""

from __future__ import annotations

import json
import logging
from collections import deque
from pathlib import Path
from time import monotonic, perf_counter
from typing import Any

from Isabella.Core.config import ConfigurationError, PROJECT_ROOT
from .camera import CameraCapture
from .models import ImageCapture, ScreenAnalysis, VisionAnalysisResult, VisionResult
from .provider import OllamaVisionProvider, VisionProviderError
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
        "multimodal_enabled", "vision_provider", "vision_model", "max_image_size",
        "compression_quality", "provider_local", "allow_cloud_upload", "analysis_context_ttl_seconds",
    }
    if not isinstance(config, dict) or required - config.keys():
        raise ConfigurationError("Vision configuration is missing required fields")
    if config["continuous_capture"] is not False:
        raise ConfigurationError("Continuous Vision capture is disabled in this phase")
    if int(config["max_image_width"]) < 1 or int(config["max_image_height"]) < 1:
        raise ConfigurationError("Vision image limits are invalid")
    if not 256 <= int(config["max_image_size"]) <= 4096 or not 30 <= int(config["compression_quality"]) <= 100:
        raise ConfigurationError("Vision multimodal image limits are invalid")
    if bool(config["multimodal_enabled"]) and config["vision_provider"] != "ollama":
        raise ConfigurationError("Only the local Ollama vision provider is supported")
    if bool(config["multimodal_enabled"]) and not config["vision_model"]:
        raise ConfigurationError("A separate vision model is required")
    if not bool(config["provider_local"]) and not bool(config["allow_cloud_upload"]):
        raise ConfigurationError("Cloud vision requires explicit upload permission")
    if not 5 <= float(config["analysis_context_ttl_seconds"]) <= 600:
        raise ConfigurationError("Vision analysis context TTL is invalid")
    return config


class VisionManager:
    def __init__(self, config: dict[str, Any], screen=None, camera=None, context=None, event_bus=None, provider=None) -> None:
        self.config = config
        self.enabled = bool(config["enabled"])
        self.context = context
        self.event_bus = event_bus
        width, height = int(config["max_image_width"]), int(config["max_image_height"])
        self.screen = screen or ScreenCapturer(width, height)
        self.camera = camera or CameraCapture(config.get("camera_device"), width, height)
        self.provider = provider
        self.status = "ONLINE" if self.enabled else "OFFLINE"
        self._temporary_paths: deque[Path] = deque()
        self._last_analysis: ScreenAnalysis | None = None
        self._last_analysis_at = 0.0
        self.metrics = {
            "screen_capture_ms": self.screen.latencies_ms, "camera_capture_ms": self.camera.latencies_ms,
            "capture_ms": deque(maxlen=200), "preprocess_ms": deque(maxlen=200),
            "vision_inference_ms": deque(maxlen=200), "total_ms": deque(maxlen=200),
        }

    @classmethod
    def from_config(cls, context=None, path: Path | None = None, event_bus=None) -> "VisionManager":
        config = load_vision_config(path)
        provider = None
        if config["multimodal_enabled"]:
            from Isabella.Intelligence.llm import load_intelligence_config

            intelligence = load_intelligence_config()
            provider = OllamaVisionProvider(
                config["vision_model"], intelligence["base_url"], float(intelligence["timeout_seconds"]),
                int(config["max_image_size"]), int(config["compression_quality"]),
            )
        return cls(config, context=context, event_bus=event_bus, provider=provider)

    def analyze_screen(self, question: str, *, active_window: bool = False) -> VisionAnalysisResult:
        total_started = perf_counter()
        source = "ACTIVE_WINDOW" if active_window else "SCREEN"
        if self.event_bus:
            self.event_bus.emit(EventType.VISION_ANALYSIS_STARTED, "vision", {"source": source})
        capture_started = perf_counter()
        captured = self.capture_active_window() if active_window else self.capture_screen()
        capture_ms = (perf_counter() - capture_started) * 1000
        self.metrics["capture_ms"].append(capture_ms)
        if not captured.success or not captured.capture:
            return self._analysis_failure(captured.message, captured.error_code or "CAPTURE_FAILED", source, total_started, capture_ms)
        capture = captured.capture
        try:
            if not self.config["multimodal_enabled"] or self.provider is None:
                return self._analysis_failure("O modelo visual está indisponível.", "VISION_MODEL_UNAVAILABLE", source, total_started, capture_ms)
            preprocess_started = perf_counter()
            image, metadata = self.provider.preprocess(capture)
            preprocess_ms = (perf_counter() - preprocess_started) * 1000
            self.metrics["preprocess_ms"].append(preprocess_ms)
            inference_started = perf_counter()
            analysis = self.provider.analyze(image, question)
            inference_ms = (perf_counter() - inference_started) * 1000
            self.metrics["vision_inference_ms"].append(inference_ms)
            total_ms = (perf_counter() - total_started) * 1000
            self.metrics["total_ms"].append(total_ms)
            timings = {"capture_ms": capture_ms, "preprocess_ms": preprocess_ms, "vision_inference_ms": inference_ms, "total_ms": total_ms}
            self._last_analysis = analysis
            self._last_analysis_at = monotonic()
            application = analysis.applications[0] if analysis.applications else capture.metadata.get("application")
            if self.context:
                try:
                    self.context.update(
                        last_screen_summary=analysis.summary,
                        last_detected_error=analysis.errors[0] if analysis.errors else None,
                        last_visible_application=application,
                    )
                except Exception as exc:
                    LOGGER.warning("vision_context_update_failed error=%s", type(exc).__name__)
            if self.event_bus:
                self.event_bus.emit(
                    EventType.VISION_ANALYSIS_COMPLETED, "vision",
                    {"source": source, "confidence": analysis.confidence, "timings_ms": timings, "image": metadata},
                )
            LOGGER.info(
                "analysis model=%s capture_ms=%.2f preprocess_ms=%.2f inference_ms=%.2f total_ms=%.2f",
                self.config["vision_model"], capture_ms, preprocess_ms, inference_ms, total_ms,
            )
            return VisionAnalysisResult(True, analysis.to_message(), analysis, timings_ms=timings)
        except VisionProviderError as exc:
            LOGGER.warning("vision_analysis_failed error=%s", type(exc).__name__)
            return self._analysis_failure("Não foi possível interpretar a tela agora.", "VISION_INFERENCE_FAILED", source, total_started, capture_ms)
        finally:
            self.cleanup(capture)

    def recent_analysis(self) -> ScreenAnalysis | None:
        if not self._last_analysis:
            return None
        if monotonic() - self._last_analysis_at > float(self.config["analysis_context_ttl_seconds"]):
            self._last_analysis = None
            return None
        return self._last_analysis

    def _analysis_failure(self, message: str, error_code: str, source: str, total_started: float, capture_ms: float) -> VisionAnalysisResult:
        total_ms = (perf_counter() - total_started) * 1000
        self.metrics["total_ms"].append(total_ms)
        timings = {"capture_ms": capture_ms, "preprocess_ms": 0.0, "vision_inference_ms": 0.0, "total_ms": total_ms}
        if self.event_bus:
            self.event_bus.emit(EventType.VISION_ANALYSIS_FAILED, "vision", {"source": source, "error_code": error_code})
        return VisionAnalysisResult(False, message, timings_ms=timings, error_code=error_code)

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

    def health_check(self, check_camera: bool = False) -> dict[str, Any]:
        details = {
            "screen": bool(self.enabled and self.config["screen_capture_enabled"] and self.screen.health_check()),
            "camera": self.camera.health_check() if check_camera and self.config["camera_enabled"] else None,
            "multimodal_enabled": bool(self.config["multimodal_enabled"]),
            "provider_local": bool(self.config["provider_local"]),
        }
        if self.provider:
            details.update(self.provider.health_check())
        return details

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
        if self.provider:
            self.provider.close()
        while self._temporary_paths:
            self._remove(self._temporary_paths.popleft())
        self.status = "OFFLINE"
