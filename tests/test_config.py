import json

import pytest

from Isabella.Core.config import ConfigurationError, load_config


VALID_CONFIG = {
    "name": "ISABELLA",
    "full_name": "I.S.A.B.E.L.L.A.",
    "acronym": "Intelligent System for Adaptive Behavior, Environment, Learning, Logic and Assistance",
    "debug": True,
}


def test_load_valid_config(tmp_path):
    config_path = tmp_path / "system.json"
    config_path.write_text(json.dumps(VALID_CONFIG), encoding="utf-8")

    assert load_config(config_path) == VALID_CONFIG


def test_missing_config_has_clear_error(tmp_path):
    config_path = tmp_path / "missing.json"

    with pytest.raises(ConfigurationError, match="Configuration file not found"):
        load_config(config_path)


def test_invalid_json_has_clear_error(tmp_path):
    config_path = tmp_path / "system.json"
    config_path.write_text("{invalid", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="Invalid JSON"):
        load_config(config_path)
