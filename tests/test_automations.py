from pathlib import Path

import pytest

from Isabella.Automations import AutomationManager, ConditionOperator, AutomationCondition
from Isabella.Automations.conditions import matches
from Isabella.Events import Event, EventType
from Isabella.Security import SecurityPolicyEngine
from Isabella.Skills.automations import create_automation_skills
from Isabella.Skills.base import ParameterSpec, RiskLevel, SkillDefinition, SkillResult
from Isabella.Skills.registry import SkillRegistry


def config(path: Path, max_depth=5):
    return {
        "enabled": True, "database_path": str(path), "default_cooldown_seconds": 0,
        "max_chain_depth": max_depth, "max_actions": 8, "max_action_retries": 0,
    }


class Bus:
    def __init__(self):
        self.subscribers = set()
        self.events = []

    def subscribe(self, pattern, callback):
        self.subscribers.add(callback)

    def unsubscribe(self, pattern, callback):
        self.subscribers.discard(callback)
        return True

    def emit(self, event_type, source, payload=None, **kwargs):
        name = event_type.value if hasattr(event_type, "value") else event_type
        self.events.append((name, payload or {}, kwargs.get("correlation_id")))
        return True


def registry(executions=None, fail=False):
    executions = executions if executions is not None else []
    policy = SecurityPolicyEngine({
        "confirmation_timeout_seconds": 60,
        "risk_policies": {"SAFE": "ALLOW", "CAUTION": "ALLOW", "CRITICAL": "CONFIRM"},
        "critical_confirmation_required": True, "logging_level": "INFO",
    })
    result = SkillRegistry(policy_engine=policy)

    def execute(arguments):
        executions.append(arguments)
        return SkillResult(not fail, "test.safe", "ok" if not fail else "failed", error_code="FAILED" if fail else None, status="completed" if not fail else "failed")

    result.register(SkillDefinition("test.safe", "Safe", "test", "test", {"value": ParameterSpec(int)}, RiskLevel.SAFE, execute))
    result.register(SkillDefinition("test.critical", "Critical", "test", "test", {}, RiskLevel.CRITICAL, lambda arguments: SkillResult(True, "test.critical", "must not run")))
    return result


def spec(automation_id="rule.llm_offline", enabled=True, skill="test.safe", cooldown=0):
    return {
        "id": automation_id, "name": "LLM offline", "enabled": enabled,
        "trigger": {"type": "EVENT", "event": "diagnostics.status_changed"},
        "conditions": [
            {"field": "subsystem", "operator": "equals", "value": "LLM"},
            {"field": "status", "operator": "equals", "value": "OFFLINE"},
        ],
        "actions": [{"skill": skill, "arguments": {"value": 1} if skill == "test.safe" else {}}],
        "owner": "user", "source": "manual_structured", "cooldown_seconds": cooldown,
    }


def test_condition_operators():
    payload = {"value": 5, "text": "isabella", "nested": {"ready": True}}
    assert matches(AutomationCondition("value", ConditionOperator.EQUALS, 5), payload)
    assert matches(AutomationCondition("value", ConditionOperator.NOT_EQUALS, 4), payload)
    assert matches(AutomationCondition("value", ConditionOperator.GREATER_THAN, 4), payload)
    assert matches(AutomationCondition("value", ConditionOperator.LESS_THAN, 6), payload)
    assert matches(AutomationCondition("text", ConditionOperator.CONTAINS, "bella"), payload)
    assert matches(AutomationCondition("nested.ready", ConditionOperator.EXISTS, True), payload)


def test_event_true_executes_and_false_condition_does_not(tmp_path):
    executions, bus = [], Bus()
    manager = AutomationManager(config(tmp_path / "rules.db"), registry=registry(executions), event_bus=bus)
    manager.create_automation(spec())
    manager.start()
    manager.engine.handle_event(Event("diagnostics.status_changed", "test", {"subsystem": "LLM", "status": "ONLINE"}, "a"))
    assert executions == []
    manager.engine.handle_event(Event("diagnostics.status_changed", "test", {"subsystem": "LLM", "status": "OFFLINE"}, "b"))
    assert executions == [{"value": 1}]
    assert EventType.AUTOMATION_COMPLETED.value in {item[0] for item in bus.events}
    manager.shutdown()


def test_disabled_and_manual_runs(tmp_path):
    executions = []
    manager = AutomationManager(config(tmp_path / "rules.db"), registry=registry(executions), event_bus=Bus())
    item = manager.create_automation(spec(enabled=False))
    assert manager.run_manual(item.id).error == "AUTOMATION_DISABLED"
    manager.enable(item.id)
    assert manager.run_manual(item.id).success
    manager.disable(item.id)
    manager.engine.handle_event(Event("diagnostics.status_changed", "test", {"subsystem": "LLM", "status": "OFFLINE"}))
    assert len(executions) == 1


def test_critical_action_is_never_preconfirmed(tmp_path):
    manager = AutomationManager(config(tmp_path / "rules.db"), registry=registry(), event_bus=Bus())
    item = manager.create_automation(spec(skill="test.critical"))
    run = manager.run_manual(item.id)
    assert not run.success
    assert run.error == "CONFIRMATION_REQUIRED"
    assert run.results[0]["status"] == "confirmation_required"


def test_cooldown_and_max_runs(tmp_path):
    manager = AutomationManager(config(tmp_path / "rules.db"), registry=registry(), event_bus=Bus())
    data = spec(cooldown=60)
    data["max_runs"] = 1
    item = manager.create_automation(data)
    assert manager.run_manual(item.id).success
    assert manager.run_manual(item.id).error in {"MAX_RUNS_REACHED", "COOLDOWN_ACTIVE"}


def test_chain_depth_blocks_repeated_correlation(tmp_path):
    bus = Bus()
    manager = AutomationManager(config(tmp_path / "rules.db", max_depth=2), registry=registry(), event_bus=bus)
    item = manager.create_automation(spec())
    assert manager.engine.run(item, correlation_id="chain").success
    item = manager.get(item.id)
    assert manager.engine.run(item, correlation_id="chain").success
    item = manager.get(item.id)
    assert manager.engine.run(item, correlation_id="chain").error == "MAX_CHAIN_DEPTH"
    assert EventType.AUTOMATION_LOOP_BLOCKED.value in {event[0] for event in bus.events}


def test_failed_action_is_recorded_without_retry(tmp_path):
    executions = []
    manager = AutomationManager(config(tmp_path / "rules.db"), registry=registry(executions, fail=True), event_bus=Bus())
    item = manager.create_automation(spec())
    assert not manager.run_manual(item.id).success
    assert len(executions) == 1
    assert manager.diagnostics()["failures"] == 1
    assert manager.get(item.id).run_count == 1
    assert manager.diagnostics()["last_execution"] is not None


def test_crud_persistence_and_validation(tmp_path):
    path = tmp_path / "rules.db"
    manager = AutomationManager(config(path), registry=registry(), event_bus=Bus())
    item = manager.create_automation(spec())
    assert manager.get(item.id).name == "LLM offline"
    assert manager.update_automation(item.id, {"name": "Changed"}).name == "Changed"
    assert manager.diagnostics()["automations_total"] == 1
    assert manager.delete(item.id)
    assert manager.list() == []
    with pytest.raises(ValueError):
        manager.create_automation({**spec("bad.rule"), "actions": [{"skill": "unknown.skill", "arguments": {}}]})


def test_automation_skills_have_conservative_risks(tmp_path):
    manager = AutomationManager(config(tmp_path / "rules.db"), registry=registry(), event_bus=Bus())
    definitions = {item.id: item for item in create_automation_skills(manager)}
    assert definitions["automations.create"].risk_level is RiskLevel.CRITICAL
    assert definitions["automations.list"].risk_level is RiskLevel.SAFE
    assert definitions["automations.enable"].risk_level is RiskLevel.CAUTION


def test_irrelevant_event_stress_has_no_actions(tmp_path):
    executions = []
    manager = AutomationManager(config(tmp_path / "rules.db"), registry=registry(executions), event_bus=Bus())
    manager.create_automation(spec())
    for index in range(10_000):
        manager.engine.handle_event(Event("unrelated.event", "stress", {"index": index}))
    assert executions == []
