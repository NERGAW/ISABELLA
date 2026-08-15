"""Voice pipeline models and centralized configuration."""

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from Isabella.Core.config import ConfigurationError, PROJECT_ROOT


DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "voice.json"
REQUIRED_FIELDS = {
    "enabled", "language", "wake_word", "wake_word_aliases", "model_size",
    "device", "compute_type", "input_device", "sample_rate", "vad_enabled",
    "command_timeout_seconds", "debug_transcription",
}


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str
    confidence: float | None
    duration_ms: float
    latency_ms: float


@dataclass(frozen=True)
class VoiceCommand:
    raw_text: str
    normalized_text: str
    wake_word_detected: bool
    command_text: str
    wake_alias: str | None = None


def load_voice_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or DEFAULT_CONFIG_PATH
    if not config_path.is_file():
        raise ConfigurationError(f"Voice configuration not found: {config_path}")
    try:
        with config_path.open(encoding="utf-8") as config_file:
            config = json.load(config_file)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"Invalid voice JSON: {exc.msg}") from exc
    missing = sorted(REQUIRED_FIELDS - config.keys()) if isinstance(config, dict) else []
    if not isinstance(config, dict) or missing:
        raise ConfigurationError(
            "Invalid voice configuration" + (f"; missing: {', '.join(missing)}" if missing else "")
        )
    if config["sample_rate"] <= 0 or config["command_timeout_seconds"] <= 0:
        raise ConfigurationError("Voice timing and sample rate must be positive")
    if not isinstance(config["wake_word_aliases"], list) or not config["wake_word_aliases"]:
        raise ConfigurationError("At least one wake word alias is required")
    return config
