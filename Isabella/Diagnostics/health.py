"""Small health classification helpers; no service lifecycle ownership."""

from __future__ import annotations

from pathlib import Path
from time import perf_counter

from .models import HealthStatus, Subsystem, SubsystemHealth


def health(subsystem: Subsystem, probe) -> SubsystemHealth:
    started = perf_counter()
    try:
        status, details = probe()
    except Exception as exc:
        status, details = HealthStatus.ERROR, {"error": type(exc).__name__}
    return SubsystemHealth(
        subsystem, status, details,
        latency_ms=(perf_counter() - started) * 1000,
    )


def latency_details(component) -> dict[str, float]:
    values = getattr(component, "latencies_ms", ())
    latest = values[-1] if values else 0.0
    average = getattr(component, "average_latency_ms", 0.0)
    return {
        "last_request_latency_ms": round(float(latest), 3),
        "average_latency_ms": round(float(average), 3),
    }


def queue_size(component, attribute: str) -> int:
    value = getattr(component, attribute, None)
    return value.qsize() if value is not None else 0


def path_size(path) -> int:
    target = Path(path)
    return target.stat().st_size if target.exists() else 0
