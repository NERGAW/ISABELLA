import json
import logging
from logging.handlers import RotatingFileHandler

from Isabella.Core.app import IsabellaApp, IsabellaStatus
from tests.test_config import VALID_CONFIG


def make_app(tmp_path):
    config_path = tmp_path / "system.json"
    config_path.write_text(json.dumps(VALID_CONFIG), encoding="utf-8")
    return IsabellaApp(config_path=config_path, log_path=tmp_path / "isabella.log")


def test_app_initialization(tmp_path):
    app = make_app(tmp_path)

    assert app.status == IsabellaStatus.OFFLINE
    assert app.config is None


def test_start_transitions_from_starting_to_online(tmp_path):
    app = make_app(tmp_path)
    app.start()

    assert app.status == IsabellaStatus.ONLINE
    assert app.state_history[-2:] == [IsabellaStatus.STARTING, IsabellaStatus.ONLINE]


def test_shutdown_transitions_through_stopping_to_offline(tmp_path):
    app = make_app(tmp_path)
    app.start()
    app.shutdown()

    assert app.status == IsabellaStatus.OFFLINE
    assert app.state_history[-3:] == [
        IsabellaStatus.ONLINE,
        IsabellaStatus.STOPPING,
        IsabellaStatus.OFFLINE,
    ]


def test_file_logging_is_bounded_by_rotation(tmp_path):
    app = make_app(tmp_path)
    app.start()
    handler = next(
        item for item in logging.getLogger().handlers
        if isinstance(item, RotatingFileHandler)
    )
    assert handler.maxBytes == 5 * 1024 * 1024
    assert handler.backupCount == 3
    app.shutdown()
