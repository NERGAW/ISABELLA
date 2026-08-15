"""Efficient next-deadline worker and recurrence calculations."""

from __future__ import annotations

from datetime import datetime, timedelta
import threading
from typing import Callable
from zoneinfo import ZoneInfo

from .models import ScheduleType, ScheduledTask, TaskStatus, parse_aware


def next_occurrence(task: ScheduledTask, after: datetime, timezone: ZoneInfo) -> datetime | None:
    if after.tzinfo is None:
        raise ValueError("Current time must be timezone-aware")
    if task.schedule_type is ScheduleType.ONE_TIME:
        return parse_aware(task.run_at) if task.run_at else None
    if task.schedule_type is ScheduleType.INTERVAL:
        seconds = int(task.recurrence.get("interval_seconds", 0))
        if seconds < 1:
            raise ValueError("Interval must be at least one second")
        base = parse_aware(task.next_run or task.run_at) if (task.next_run or task.run_at) else after
        while base <= after:
            base += timedelta(seconds=seconds)
        return base
    hour = int(task.recurrence["hour"])
    minute = int(task.recurrence.get("minute", 0))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("Recurrence time is invalid")
    local = after.astimezone(timezone)
    if task.schedule_type is ScheduleType.DAILY:
        candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= local:
            candidate += timedelta(days=1)
        return candidate
    weekdays = sorted(set(int(item) for item in task.recurrence.get("weekdays", [])))
    if not weekdays or any(item < 0 or item > 6 for item in weekdays):
        raise ValueError("Weekly weekdays are invalid")
    for offset in range(8):
        day = local + timedelta(days=offset)
        candidate = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if day.weekday() in weekdays and candidate > local:
            return candidate
    raise ValueError("Unable to calculate weekly occurrence")


class TaskScheduler:
    def __init__(self, *, list_tasks: Callable[[], list[ScheduledTask]], execute: Callable[[ScheduledTask, datetime], None], now: Callable[[], datetime], max_sleep_seconds: float = 60) -> None:
        self.list_tasks = list_tasks
        self.execute = execute
        self.now = now
        self.max_sleep_seconds = max_sleep_seconds
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="IsabellaScheduler", daemon=True)
        self._thread.start()
        return True

    def notify_changed(self) -> None:
        self._wake.set()

    def run_due(self) -> int:
        now = self.now()
        due = [
            task for task in self.list_tasks()
            if task.enabled and task.status is TaskStatus.PENDING and task.next_run
            and parse_aware(task.next_run) <= now
        ]
        for task in due:
            self.execute(task, now)
        return len(due)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.run_due()
            now = self.now()
            future = [parse_aware(item.next_run) for item in self.list_tasks() if item.enabled and item.status is TaskStatus.PENDING and item.next_run]
            delay = min(max(0.0, (min(future) - now).total_seconds()), self.max_sleep_seconds) if future else self.max_sleep_seconds
            self._wake.wait(delay)
            self._wake.clear()

    def shutdown(self) -> bool:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(2)
        return not bool(self._thread and self._thread.is_alive())

