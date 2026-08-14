"""Deterministic structure-only planner for compound requests."""

import logging
import re
from time import perf_counter

from .models import Plan, PlanStep
from .router import Router


LOGGER = logging.getLogger("PLANNER")


class Planner:
    def __init__(self, max_steps: int = 8, router: Router | None = None) -> None:
        self.max_steps = max_steps
        self.router = router or Router()
        self.latencies_ms: list[float] = []

    def plan(self, text: str) -> Plan:
        started = perf_counter()
        parts = [part.strip(" ,.") for part in re.split(r"\b(?:e depois|depois|e então|e)\b|,", text, flags=re.IGNORECASE) if part.strip(" ,.")]
        requests = [self.router.skill_request(part) for part in parts]
        if len(requests) > self.max_steps:
            result = Plan([], error=f"plan exceeds maximum of {self.max_steps} steps")
        else:
            steps = [
                PlanStep(index, request.skill, request.arguments, [index - 1] if index > 1 else [])
                for index, request in enumerate(requests, start=1)
            ]
            result = Plan(steps)
        latency = (perf_counter() - started) * 1000
        self.latencies_ms.append(latency)
        LOGGER.info("steps=%d latency_ms=%.3f error=%s", len(result.steps), latency, bool(result.error))
        return result

    @property
    def average_latency_ms(self) -> float:
        return sum(self.latencies_ms) / len(self.latencies_ms) if self.latencies_ms else 0.0
