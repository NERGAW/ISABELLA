"""Central configuration loading for ISABELLA."""

import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "system.json"
REQUIRED_FIELDS = {
    "name",
    "full_name",
    "acronym",
    "language",
    "wake_word",
    "wake_word_aliases",
    "debug",
}


class ConfigurationError(RuntimeError):
    """Raised when the central configuration cannot be loaded or validated."""


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    """Load and validate the central JSON configuration."""
    path = config_path or DEFAULT_CONFIG_PATH

    if not path.is_file():
        raise ConfigurationError(f"Configuration file not found: {path}")

    try:
        with path.open(encoding="utf-8") as config_file:
            config = json.load(config_file)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            f"Invalid JSON in configuration file {path}: {exc.msg} "
            f"(line {exc.lineno}, column {exc.colno})"
        ) from exc

    if not isinstance(config, dict):
        raise ConfigurationError(f"Configuration root must be a JSON object: {path}")

    missing_fields = sorted(REQUIRED_FIELDS - config.keys())
    if missing_fields:
        missing = ", ".join(missing_fields)
        raise ConfigurationError(f"Missing required configuration fields: {missing}")

    return config
