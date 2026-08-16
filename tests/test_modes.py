import copy

import pytest

from Isabella.Events import EventType
from Isabella.Intelligence.models import Intent
from Isabella.Intelligence.router import Router
from Isabella.Modes import ModeManager, load_modes_config
from Isabella.Skills.modes import create_mode_skill


class Bus:
    def __init__(self): self.events = []
    def emit(self, event_type, source, payload):
        self.events.append((getattr(event_type, "value", event_type), source, payload))


class Context:
    def __init__(self): self.values = {}
    def set(self, key, value): self.values[key] = value


@pytest.fixture
def manager():
    return ModeManager(load_modes_config(), event_bus=Bus(), context=Context())


@pytest.mark.parametrize("mode", ["NORMAL", "ENGINEERING", "PRIVACY", "OFFLINE", "HOME", "MOBILE"])
def test_all_initial_modes(manager, mode):
    selected = manager.set_mode(mode)
    assert selected.id == mode
    assert manager.get_current_mode().id == mode
    assert manager.context.values["current_mode"] == mode


def test_aliases_invalid_and_events(manager):
    assert manager.set_mode("Engenharia").id == "ENGINEERING"
    assert EventType.MODE_CHANGED.value in [item[0] for item in manager.event_bus.events]
    with pytest.raises(ValueError): manager.set_mode("EMERGENCY")
    assert manager.event_bus.events[-1][0] == EventType.MODE_FAILED.value


def test_privacy_offline_and_mobile_policy(manager):
    manager.set_mode("PRIVACY")
    policy = manager.apply_policy()
    assert not policy.research_allowed and policy.network_policy == "local_only"
    assert not policy.allows_skill("mcp.github.create_issue")
    manager.set_mode("OFFLINE")
    assert not manager.apply_policy().research_allowed
    manager.set_mode("NORMAL")
    assert manager.apply_policy(input_source="MOBILE_NODE").mode_id == "MOBILE"


def test_modes_cannot_weaken_security():
    config = copy.deepcopy(load_modes_config())
    config["modes"][0]["disabled_skills"] = ["security.*"]
    with pytest.raises(ValueError): ModeManager(config)


def test_safe_mode_skill_and_router(manager):
    skill = create_mode_skill(manager)
    assert skill.risk_level.value == "SAFE"
    result = skill.executor({"mode": "Privacidade"})
    assert result.success and result.data["mode"] == "PRIVACY"
    router = Router()
    assert router.route("Isabella, modo Engenharia") is Intent.SINGLE_SKILL
    assert router.skill_request("Isabella, modo Engenharia").skill == "system.set_mode"
