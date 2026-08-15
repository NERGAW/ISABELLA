"""Scheduler CRUD, missed-task policy, execution and limited natural parsing."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import re
from typing import Any, Callable
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from Isabella.Core.config import ConfigurationError, PROJECT_ROOT
from Isabella.Events import EventType
from Isabella.Skills.base import RiskLevel
from .models import MissedTaskPolicy, ScheduledTask, ScheduleType, TaskStatus, parse_aware
from .scheduler import TaskScheduler, next_occurrence
from .storage import SchedulerStorage


DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "scheduler.json"


def load_scheduler_config(path: Path | None = None) -> dict[str, Any]:
    target = path or DEFAULT_CONFIG_PATH
    try:
        config = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Invalid scheduler configuration: {target}") from exc
    required = {"enabled", "database_path", "timezone", "missed_task_policy", "max_tasks", "max_sleep_seconds"}
    if not isinstance(config, dict) or required - config.keys():
        raise ConfigurationError("Scheduler configuration is missing required fields")
    try:
        ZoneInfo(config["timezone"])
        MissedTaskPolicy(config["missed_task_policy"])
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ConfigurationError("Scheduler timezone or missed-task policy is invalid") from exc
    if not 1 <= int(config["max_tasks"]) <= 10000 or not 0.1 <= float(config["max_sleep_seconds"]) <= 3600:
        raise ConfigurationError("Scheduler limits are invalid")
    return config


class SchedulerManager:
    def __init__(self, config: dict[str, Any], *, registry, event_bus=None, storage: SchedulerStorage | None = None, now: Callable[[], datetime] | None = None, notifier: Callable[[str], object] | None = None) -> None:
        self.config = config
        self.enabled = bool(config["enabled"])
        self.registry = registry
        self.event_bus = event_bus
        self.timezone = ZoneInfo(config["timezone"])
        self.missed_policy = MissedTaskPolicy(config["missed_task_policy"])
        path = Path(config["database_path"])
        self.storage = storage or SchedulerStorage(path if path.is_absolute() else PROJECT_ROOT / path)
        self.now = now or (lambda: datetime.now(self.timezone))
        self.notifier = notifier
        self.failed_count = sum(item.status is TaskStatus.FAILED for item in self.storage.list())
        self.scheduler = TaskScheduler(list_tasks=self.list, execute=self._execute_due, now=self.now, max_sleep_seconds=float(config["max_sleep_seconds"]))

    @classmethod
    def from_config(cls, *, registry, event_bus=None, path: Path | None = None, **kwargs) -> "SchedulerManager":
        return cls(load_scheduler_config(path), registry=registry, event_bus=event_bus, **kwargs)

    def bind_notifier(self, notifier: Callable[[str], object] | None) -> None:
        self.notifier = notifier

    def start(self) -> bool:
        if not self.enabled:
            return True
        self._handle_missed_tasks()
        return self.scheduler.start()

    def create_task(self, specification: dict[str, Any]) -> ScheduledTask:
        if len(self.list()) >= int(self.config["max_tasks"]):
            raise RuntimeError("Scheduler task limit reached")
        schedule_type = ScheduleType(specification["schedule_type"])
        skill = str(specification["skill"])
        arguments = dict(specification.get("arguments", {}))
        definition = self.registry.get(skill)
        validation = self.registry.validate_arguments(skill, arguments)
        if definition is None or validation:
            raise ValueError(f"Invalid scheduled Skill: {getattr(validation, 'error_code', 'UNKNOWN_SKILL')}")
        now = self._aware_now()
        run_at = specification.get("run_at")
        if run_at:
            run_at = parse_aware(run_at).astimezone(self.timezone).isoformat()
        task = ScheduledTask(
            id=str(specification.get("id") or f"task.{uuid.uuid4().hex}"),
            name=str(specification.get("name") or skill), enabled=bool(specification.get("enabled", True)),
            schedule_type=schedule_type, run_at=run_at, recurrence=dict(specification.get("recurrence", {})),
            skill=skill, arguments=arguments, risk_level=definition.risk_level,
            created_at=now.isoformat(), next_run=None, reminder_text=specification.get("reminder_text"),
        )
        if self.storage.get(task.id):
            raise ValueError("Scheduled task id already exists")
        task.next_run = self._initial_next_run(task, now).isoformat()
        self.storage.save(task)
        self.scheduler.notify_changed()
        self._emit(EventType.SCHEDULER_TASK_CREATED, task)
        return task

    def list(self) -> list[ScheduledTask]:
        return self.storage.list()

    def get(self, task_id: str) -> ScheduledTask | None:
        return self.storage.get(task_id)

    def cancel(self, task_id: str) -> ScheduledTask:
        task = self._require(task_id)
        task.enabled = False
        task.status = TaskStatus.CANCELLED
        task.next_run = None
        self.storage.save(task)
        self.scheduler.notify_changed()
        self._emit(EventType.SCHEDULER_TASK_CANCELLED, task)
        return task

    def pause(self, task_id: str) -> ScheduledTask:
        task = self._require(task_id)
        task.enabled = False
        task.status = TaskStatus.PAUSED
        self.storage.save(task)
        self.scheduler.notify_changed()
        return task

    def resume(self, task_id: str) -> ScheduledTask:
        task = self._require(task_id)
        if task.status is TaskStatus.CANCELLED or task.schedule_type is ScheduleType.ONE_TIME and task.status is TaskStatus.COMPLETED:
            raise ValueError("Completed or cancelled task cannot be resumed")
        now = self._aware_now()
        task.enabled = True
        task.status = TaskStatus.PENDING
        if not task.next_run or parse_aware(task.next_run) <= now:
            task.next_run = self._initial_next_run(task, now).isoformat()
        self.storage.save(task)
        self.scheduler.notify_changed()
        return task

    def parse_natural_schedule(self, text: str, now: datetime | None = None) -> dict[str, Any]:
        current = (now or self._aware_now()).astimezone(self.timezone)
        normalized = text.casefold()
        if re.search(r"amanh[ãa]\s+(?:de\s+)?manh[ãa]", normalized):
            raise ValueError("Horário ambíguo; informe uma hora exata")
        relative = re.search(r"daqui\s+a\s+(\d+)\s*(minuto|hora)s?", normalized)
        if relative:
            amount = int(relative.group(1))
            delta = timedelta(minutes=amount) if relative.group(2) == "minuto" else timedelta(hours=amount)
            return {"schedule_type": "ONE_TIME", "run_at": (current + delta).isoformat()}
        daily = re.search(r"tod(?:o|os)\s+(?:os\s+)?dia(?:s)?\s+(?:à|a)s?\s+(\d{1,2})(?::(\d{2}))?", normalized)
        if daily:
            return {"schedule_type": "DAILY", "recurrence": {"hour": int(daily.group(1)), "minute": int(daily.group(2) or 0)}}
        tomorrow = re.search(r"amanh[ãa]\s+(?:à|a)s?\s+(\d{1,2})(?::(\d{2}))?", normalized)
        if tomorrow:
            target = (current + timedelta(days=1)).replace(hour=int(tomorrow.group(1)), minute=int(tomorrow.group(2) or 0), second=0, microsecond=0)
            return {"schedule_type": "ONE_TIME", "run_at": target.isoformat()}
        at_time = re.search(r"(?:à|a)s?\s+(\d{1,2})(?::(\d{2}))?(?:\s*horas?)?", normalized)
        if at_time:
            target = current.replace(hour=int(at_time.group(1)), minute=int(at_time.group(2) or 0), second=0, microsecond=0)
            if target <= current:
                target += timedelta(days=1)
            return {"schedule_type": "ONE_TIME", "run_at": target.isoformat()}
        raise ValueError("Não foi possível determinar um horário exato")

    def notify_reminder(self, text: str) -> None:
        if self.event_bus:
            self.event_bus.emit(EventType.SCHEDULER_REMINDER, "scheduler", {"message": text})
            self.event_bus.emit(EventType.UI_MESSAGE, "scheduler", {"message": text})
        if self.notifier:
            self.notifier(text)

    def diagnostics(self) -> dict[str, Any]:
        tasks = self.list()
        pending = sorted((item.next_run for item in tasks if item.enabled and item.next_run), key=str)
        return {"enabled": self.enabled, "storage_accessible": self.storage.health_check(), "scheduled_tasks": len(tasks), "next_task": pending[0] if pending else None, "failed_tasks": sum(item.status is TaskStatus.FAILED for item in tasks)}

    def shutdown(self) -> bool:
        stopped = self.scheduler.shutdown()
        self.storage.close()
        return stopped

    def _initial_next_run(self, task: ScheduledTask, now: datetime) -> datetime:
        if task.schedule_type is ScheduleType.ONE_TIME:
            if not task.run_at:
                raise ValueError("ONE_TIME requires run_at")
            result = parse_aware(task.run_at)
            if result <= now:
                raise ValueError("Scheduled date must be in the future")
            return result
        return next_occurrence(task, now, self.timezone)

    def _execute_due(self, task: ScheduledTask, now: datetime) -> None:
        self._emit(EventType.SCHEDULER_TASK_DUE, task)
        self._emit(EventType.SCHEDULER_TASK_STARTED, task)
        task.run_count += 1
        task.last_run = now.isoformat()
        result = self.registry.execute(task.skill, task.arguments, source_request_id=f"scheduler:{task.id}")
        success = result.success
        if not success:
            self.failed_count += 1
        if task.schedule_type is ScheduleType.ONE_TIME:
            task.enabled = False
            task.next_run = None
            task.status = TaskStatus.COMPLETED if success else TaskStatus.FAILED
        else:
            task.status = TaskStatus.PENDING if success else TaskStatus.FAILED
            task.next_run = next_occurrence(task, now, self.timezone).isoformat()
            task.enabled = True
        self.storage.save(task)
        self._emit(EventType.SCHEDULER_TASK_COMPLETED if success else EventType.SCHEDULER_TASK_FAILED, task, {"error_code": result.error_code})

    def _handle_missed_tasks(self) -> None:
        now = self._aware_now()
        for task in self.list():
            if not task.enabled or task.status is not TaskStatus.PENDING or not task.next_run or parse_aware(task.next_run) > now:
                continue
            if self.missed_policy is MissedTaskPolicy.RUN_ON_STARTUP:
                continue
            if self.missed_policy is MissedTaskPolicy.ASK:
                task.enabled = False
                task.status = TaskStatus.PAUSED
                self._emit(EventType.SCHEDULER_TASK_DUE, task, {"missed": True, "requires_confirmation": True})
            elif task.schedule_type is ScheduleType.ONE_TIME:
                task.enabled = False
                task.status = TaskStatus.MISSED
                task.next_run = None
            else:
                task.next_run = next_occurrence(task, now, self.timezone).isoformat()
            self.storage.save(task)

    def _aware_now(self) -> datetime:
        value = self.now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Scheduler clock must be timezone-aware")
        return value.astimezone(self.timezone)

    def _require(self, task_id: str) -> ScheduledTask:
        task = self.storage.get(task_id)
        if task is None:
            raise KeyError(f"Unknown scheduled task: {task_id}")
        return task

    def _emit(self, event_type, task: ScheduledTask, extra: dict[str, Any] | None = None) -> None:
        if self.event_bus:
            payload = {"task_id": task.id, "skill": task.skill, "next_run": task.next_run, "status": task.status.value}
            payload.update(extra or {})
            self.event_bus.emit(event_type, "scheduler", payload, correlation_id=f"scheduler:{task.id}:{task.run_count}")

