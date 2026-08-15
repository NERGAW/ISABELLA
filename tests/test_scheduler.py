from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from Isabella.Events import EventType
from Isabella.Intelligence.brain import Brain
from Isabella.Scheduler import SchedulerManager, ScheduleType, TaskStatus
from Isabella.Security import SecurityPolicyEngine
from Isabella.Skills.base import ParameterSpec, RiskLevel, SkillDefinition, SkillResult
from Isabella.Skills.registry import SkillRegistry
from Isabella.Skills.scheduler import create_scheduler_skills


ZONE = ZoneInfo("America/Sao_Paulo")


class Clock:
    def __init__(self, value):
        self.value = value

    def now(self):
        return self.value


class Bus:
    def __init__(self):
        self.events = []

    def emit(self, event_type, source, payload=None, **kwargs):
        name = event_type.value if hasattr(event_type, "value") else event_type
        self.events.append((name, payload or {}))
        return True


def config(path: Path, policy="SKIP"):
    return {"enabled": True, "database_path": str(path), "timezone": "America/Sao_Paulo", "missed_task_policy": policy, "max_tasks": 100, "max_sleep_seconds": 60}


def registry(executions=None):
    executions = executions if executions is not None else []
    security = SecurityPolicyEngine({"confirmation_timeout_seconds": 30, "risk_policies": {"SAFE": "ALLOW", "CAUTION": "ALLOW", "CRITICAL": "CONFIRM"}, "critical_confirmation_required": True, "logging_level": "INFO"})
    result = SkillRegistry(policy_engine=security)

    def safe(arguments):
        executions.append(arguments)
        return SkillResult(True, "test.safe", "ok")

    result.register(SkillDefinition("test.safe", "Safe", "test", "test", {"value": ParameterSpec(int)}, RiskLevel.SAFE, safe))
    result.register(SkillDefinition("test.critical", "Critical", "test", "test", {}, RiskLevel.CRITICAL, lambda arguments: SkillResult(True, "test.critical", "unsafe")))
    return result


def manager(tmp_path, clock, executions=None, policy="SKIP", bus=None):
    return SchedulerManager(config(tmp_path / "scheduler.db", policy), registry=registry(executions), event_bus=bus or Bus(), now=clock.now)


def one_time(when, skill="test.safe"):
    return {"id": "task.once", "name": "Once", "schedule_type": "ONE_TIME", "run_at": when.isoformat(), "skill": skill, "arguments": {"value": 1} if skill == "test.safe" else {}}


def test_one_time_executes_once_with_mocked_clock(tmp_path):
    clock = Clock(datetime(2026, 8, 15, 10, 0, tzinfo=ZONE))
    executions, bus = [], Bus()
    scheduler = manager(tmp_path, clock, executions, bus=bus)
    task = scheduler.create_task(one_time(clock.value + timedelta(minutes=1)))
    assert scheduler.scheduler.run_due() == 0
    clock.value += timedelta(minutes=1)
    assert scheduler.scheduler.run_due() == 1
    stored = scheduler.get(task.id)
    assert executions == [{"value": 1}]
    assert stored.status is TaskStatus.COMPLETED and not stored.enabled and stored.run_count == 1
    assert EventType.SCHEDULER_TASK_DUE.value in {item[0] for item in bus.events}


@pytest.mark.parametrize("schedule_type,recurrence,expected", [
    ("INTERVAL", {"interval_seconds": 300}, datetime(2026, 8, 15, 10, 5, tzinfo=ZONE)),
    ("DAILY", {"hour": 11, "minute": 30}, datetime(2026, 8, 15, 11, 30, tzinfo=ZONE)),
    ("WEEKLY", {"weekdays": [6], "hour": 9, "minute": 0}, datetime(2026, 8, 16, 9, 0, tzinfo=ZONE)),
])
def test_recurring_next_run(schedule_type, recurrence, expected, tmp_path):
    clock = Clock(datetime(2026, 8, 15, 10, 0, tzinfo=ZONE))  # Saturday
    scheduler = manager(tmp_path, clock)
    task = scheduler.create_task({"id": f"task.{schedule_type.lower()}", "name": schedule_type, "schedule_type": schedule_type, "recurrence": recurrence, "skill": "test.safe", "arguments": {"value": 1}})
    assert datetime.fromisoformat(task.next_run) == expected


def test_cancel_pause_resume(tmp_path):
    clock = Clock(datetime(2026, 8, 15, 10, 0, tzinfo=ZONE))
    scheduler = manager(tmp_path, clock)
    task = scheduler.create_task(one_time(clock.value + timedelta(hours=1)))
    assert scheduler.pause(task.id).status is TaskStatus.PAUSED
    assert scheduler.resume(task.id).status is TaskStatus.PENDING
    assert scheduler.cancel(task.id).status is TaskStatus.CANCELLED
    with pytest.raises(ValueError):
        scheduler.resume(task.id)


def test_restart_persistence_and_default_skip_missed(tmp_path):
    start = datetime(2026, 8, 15, 10, 0, tzinfo=ZONE)
    first = manager(tmp_path, Clock(start))
    first.create_task(one_time(start + timedelta(minutes=1)))
    first.shutdown()
    second = manager(tmp_path, Clock(start + timedelta(hours=1)))
    second._handle_missed_tasks()
    task = second.get("task.once")
    assert task.status is TaskStatus.MISSED and task.next_run is None


def test_run_on_startup_policy_executes_missed_task(tmp_path):
    start = datetime(2026, 8, 15, 10, 0, tzinfo=ZONE)
    first = manager(tmp_path, Clock(start))
    first.create_task(one_time(start + timedelta(minutes=1)))
    first.shutdown()
    executions = []
    second = manager(tmp_path, Clock(start + timedelta(hours=1)), executions, policy="RUN_ON_STARTUP")
    second._handle_missed_tasks()
    assert second.scheduler.run_due() == 1
    assert executions == [{"value": 1}]


def test_critical_task_is_not_automatically_confirmed(tmp_path):
    clock = Clock(datetime(2026, 8, 15, 10, 0, tzinfo=ZONE))
    scheduler = manager(tmp_path, clock)
    task = scheduler.create_task(one_time(clock.value + timedelta(seconds=1), "test.critical"))
    assert task.risk_level is RiskLevel.CRITICAL
    clock.value += timedelta(seconds=1)
    scheduler.scheduler.run_due()
    assert scheduler.get(task.id).status is TaskStatus.FAILED
    assert scheduler.diagnostics()["failed_tasks"] == 1


def test_timezone_and_invalid_dates(tmp_path):
    clock = Clock(datetime(2026, 8, 15, 10, 0, tzinfo=ZONE))
    scheduler = manager(tmp_path, clock)
    task = scheduler.create_task(one_time(clock.value + timedelta(minutes=5)))
    assert datetime.fromisoformat(task.next_run).utcoffset() == timedelta(hours=-3)
    with pytest.raises(ValueError, match="timezone-aware"):
        scheduler.create_task(one_time(datetime(2026, 8, 16, 10, 0)))
    with pytest.raises(ValueError, match="future"):
        scheduler.create_task({**one_time(clock.value - timedelta(minutes=1)), "id": "task.past"})


def test_natural_schedule_and_ambiguity(tmp_path):
    now = datetime(2026, 8, 15, 10, 0, tzinfo=ZONE)
    scheduler = manager(tmp_path, Clock(now))
    assert scheduler.parse_natural_schedule("daqui a 10 minutos", now)["run_at"] == (now + timedelta(minutes=10)).isoformat()
    assert scheduler.parse_natural_schedule("amanhã às 8", now)["run_at"].startswith("2026-08-16T08:00")
    assert scheduler.parse_natural_schedule("todo dia às 19", now) == {"schedule_type": "DAILY", "recurrence": {"hour": 19, "minute": 0}}
    with pytest.raises(ValueError, match="ambíguo"):
        scheduler.parse_natural_schedule("amanhã de manhã", now)


def test_reminder_notifies_event_hud_and_tts_callback(tmp_path):
    clock = Clock(datetime(2026, 8, 15, 10, 0, tzinfo=ZONE))
    bus, spoken = Bus(), []
    skills = registry()
    scheduler = SchedulerManager(config(tmp_path / "scheduler.db"), registry=skills, event_bus=bus, now=clock.now, notifier=spoken.append)
    for definition in create_scheduler_skills(scheduler):
        skills.register(definition)
    task = scheduler.create_task({**one_time(clock.value + timedelta(seconds=1), "scheduler.reminder"), "arguments": {"text": "Beba água"}, "reminder_text": "Beba água"})
    clock.value += timedelta(seconds=1)
    scheduler.scheduler.run_due()
    assert spoken == ["Beba água"]
    assert {EventType.SCHEDULER_REMINDER.value, EventType.UI_MESSAGE.value} <= {item[0] for item in bus.events}


def test_scheduler_skills_use_conservative_risks(tmp_path):
    scheduler = manager(tmp_path, Clock(datetime(2026, 8, 15, 10, 0, tzinfo=ZONE)))
    definitions = {item.id: item for item in create_scheduler_skills(scheduler)}
    assert definitions["scheduler.create"].risk_level is RiskLevel.CRITICAL
    assert definitions["scheduler.cancel"].risk_level is RiskLevel.SAFE
    assert definitions["scheduler.resume"].risk_level is RiskLevel.CAUTION


def test_brain_transforms_natural_reminder_before_persistence(tmp_path):
    clock = Clock(datetime(2026, 8, 15, 10, 0, tzinfo=ZONE))
    skills = registry()
    scheduler = SchedulerManager(config(tmp_path / "scheduler.db"), registry=skills, event_bus=Bus(), now=clock.now)
    for definition in create_scheduler_skills(scheduler):
        skills.register(definition)
    brain = Brain(object(), registry=skills, security=skills.policy_engine, scheduler=scheduler)
    response = brain._handle_schedule_command("Isabella, me lembre de beber água daqui a 10 minutos.", "natural-test")
    result = response.skill_results[0]
    assert result.status == "confirmation_required"
    specification = result.data["arguments"]["specification"]
    assert specification["skill"] == "scheduler.reminder"
    assert specification["arguments"] == {"text": "beber água"}
    assert specification["run_at"] == (clock.value + timedelta(minutes=10)).isoformat()
    assert scheduler.list() == []
