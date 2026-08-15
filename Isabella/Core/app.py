"""Minimal lifecycle for the ISABELLA application."""

from enum import Enum
import logging
from pathlib import Path
from collections.abc import Callable
from typing import Any

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

    def _set_status(self, status: IsabellaStatus) -> None:
        self.status = status
        self.state_history.append(status)

    def start(self) -> None:
        """Load configuration and bring the application online."""
        self._set_status(IsabellaStatus.STARTING)
        self.config = load_config(self.config_path)
        self.logger = setup_logging(self.log_path, debug=self.config["debug"])

        print(self.config["full_name"])
        print(self.config["acronym"])
        print()
        self.logger.info("Configuration loaded.")
        self._set_status(IsabellaStatus.ONLINE)
        self.logger.info("ISABELLA online.")

    def start_voice(self, callback: Callable[[str], object], config_path: Path | None = None) -> bool:
        """Start optional voice input without affecting Core availability."""
        try:
            from Isabella.Voice.listener import VoiceListener
            from Isabella.Voice.models import load_voice_config

            voice_config = load_voice_config(config_path)
            if not voice_config["enabled"]:
                if self.logger:
                    self.logger.info("Voice input disabled by configuration.")
                return False
            self.voice_listener = VoiceListener(voice_config, callback)
            started = self.voice_listener.start()
            if self.logger:
                self.logger.info("Voice input started=%s", started)
            return started
        except Exception:
            if self.logger:
                self.logger.exception("Voice input unavailable; text mode remains online.")
            self.voice_listener = None
            return False

    def shutdown(self) -> None:
        """Shut down the application cleanly."""
        if self.status == IsabellaStatus.OFFLINE:
            return

        self._set_status(IsabellaStatus.STOPPING)
        if self.logger:
            self.logger.info("ISABELLA stopping.")
        if self.voice_listener:
            stopped = self.voice_listener.stop()
            if self.logger:
                self.logger.info("Voice input stopped=%s", stopped)
        self._set_status(IsabellaStatus.OFFLINE)
        if self.logger:
            self.logger.info("ISABELLA offline.")
