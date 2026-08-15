"""Minimal lifecycle for the ISABELLA application."""

from enum import Enum
import logging
from pathlib import Path
from collections.abc import Callable
from typing import Any
from time import perf_counter

from .config import load_config
from .logging_setup import setup_logging


class IsabellaStatus(str, Enum):
    STARTING = "STARTING"
    ONLINE = "ONLINE"
    STOPPING = "STOPPING"
    OFFLINE = "OFFLINE"


class IsabellaApp:
    """Coordinate configuration, logging, and the core lifecycle."""

    def __init__(self, config_path: Path | None = None, log_path: Path | None = None) -> None:
        self.config_path = config_path
        self.log_path = log_path
        self.config: dict[str, Any] | None = None
        self.logger: logging.Logger | None = None
        self.status = IsabellaStatus.OFFLINE
        self.state_history = [self.status]
        self.voice_listener: Any | None = None
        self.tts_manager: Any | None = None
        self.startup_metrics: dict[str, float] = {}
        self.event_bus: Any | None = None

    def _set_status(self, status: IsabellaStatus) -> None:
        self.status = status
        self.state_history.append(status)

    def start(self) -> None:
        """Load configuration and bring the application online."""
        total_started = perf_counter()
        self._set_status(IsabellaStatus.STARTING)
        config_started = perf_counter()
        self.config = load_config(self.config_path)
        self.startup_metrics["config_ms"] = (perf_counter() - config_started) * 1000
        self.logger = setup_logging(self.log_path, debug=self.config["debug"])
        from Isabella.Events import EventBus, EventType

        self.event_bus = EventBus.from_config()
        self.event_bus.emit(EventType.SYSTEM_STARTED, "core")

        print(self.config["full_name"])
        print(self.config["acronym"])
        print()
        self.logger.info("Configuration loaded.")
        self._set_status(IsabellaStatus.ONLINE)
        self.logger.info("ISABELLA online.")
        self.event_bus.emit(EventType.SYSTEM_READY, "core")
        self.startup_metrics["core_ms"] = (perf_counter() - total_started) * 1000
        logging.getLogger("PERFORMANCE").info(
            "startup config_ms=%.2f core_ms=%.2f",
            self.startup_metrics["config_ms"], self.startup_metrics["core_ms"],
        )

    def start_voice(self, callback: Callable[[str], object], config_path: Path | None = None) -> bool:
        """Start optional voice input without affecting Core availability."""
        started_at = perf_counter()
        try:
            from Isabella.Voice.listener import VoiceListener
            from Isabella.Voice.models import load_voice_config

            voice_config = load_voice_config(config_path)
            if not voice_config["enabled"]:
                if self.logger:
                    self.logger.info("Voice input disabled by configuration.")
                return False
            self.voice_listener = VoiceListener(voice_config, callback, event_bus=self.event_bus)
            started = self.voice_listener.start()
            if self.logger:
                self.logger.info("Voice input started=%s", started)
            return started
        except Exception:
            if self.logger:
                self.logger.exception("Voice input unavailable; text mode remains online.")
            self.voice_listener = None
            return False
        finally:
            self.startup_metrics["voice_ms"] = (perf_counter() - started_at) * 1000

    def start_tts(
        self,
        config_path: Path | None = None,
        state_callback: Callable[[bool], None] | None = None,
    ) -> bool:
        """Start optional voice output with listener echo protection."""
        started_at = perf_counter()
        try:
            from Isabella.Voice.models import load_voice_config
            from Isabella.Voice.tts import TTSManager

            tts_config = load_voice_config(config_path).get("tts", {})
            if not tts_config.get("enabled", False):
                if self.logger:
                    self.logger.info("TTS disabled by configuration.")
                return False

            def speaking_changed(speaking: bool) -> None:
                if not self.voice_listener:
                    return
                if speaking:
                    self.voice_listener.pause_for_speech()
                else:
                    self.voice_listener.resume_after_speech()
                if state_callback:
                    state_callback(speaking)

            self.tts_manager = TTSManager(
                tts_config, on_speaking_change=speaking_changed, event_bus=self.event_bus,
            )
            initialized = self.tts_manager.initialize()
            if self.logger:
                self.logger.info("TTS started=%s", initialized)
            return initialized
        except Exception:
            if self.logger:
                self.logger.exception("TTS unavailable; text mode remains online.")
            self.tts_manager = None
            return False
        finally:
            self.startup_metrics["tts_ms"] = (perf_counter() - started_at) * 1000

    def speak(self, text: str, correlation_id: str | None = None) -> bool:
        return bool(self.tts_manager and self.tts_manager.speak(text, correlation_id=correlation_id))

    def stop_tts(self) -> bool:
        if not self.tts_manager:
            return True
        stopped = self.tts_manager.shutdown()
        self.tts_manager = None
        return stopped

    def stop_voice(self) -> bool:
        if not self.voice_listener:
            return True
        stopped = self.voice_listener.stop()
        self.voice_listener = None
        return stopped

    def stop_core(self) -> bool:
        if self.status == IsabellaStatus.OFFLINE:
            return True
        self._set_status(IsabellaStatus.STOPPING)
        if self.event_bus:
            from Isabella.Events import EventType
            self.event_bus.emit(EventType.SYSTEM_STOPPING, "core")
        self._set_status(IsabellaStatus.OFFLINE)
        return True

    def stop_event_bus(self) -> bool:
        if not self.event_bus:
            return True
        stopped = self.event_bus.shutdown()
        self.event_bus = None
        return stopped

    def shutdown(self) -> None:
        """Shut down the application cleanly."""
        if self.status == IsabellaStatus.OFFLINE:
            return

        self._set_status(IsabellaStatus.STOPPING)
        if self.event_bus:
            from Isabella.Events import EventType
            self.event_bus.emit(EventType.SYSTEM_STOPPING, "core")
        if self.logger:
            self.logger.info("ISABELLA stopping.")
        if self.tts_manager:
            stopped = self.stop_tts()
            if self.logger:
                self.logger.info("TTS stopped=%s", stopped)
        if self.voice_listener:
            stopped = self.stop_voice()
            if self.logger:
                self.logger.info("Voice input stopped=%s", stopped)
        self._set_status(IsabellaStatus.OFFLINE)
        if self.logger:
            self.logger.info("ISABELLA offline.")
        self.stop_event_bus()
