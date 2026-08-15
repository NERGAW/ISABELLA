"""Small read-only technical diagnostics for the current runtime."""

from __future__ import annotations

import threading
from typing import Any

import psutil


def technical_snapshot(app, brain=None, controller=None) -> dict[str, Any]:
    """Return bounded operational data without starting any new service."""
    listener = getattr(app, "voice_listener", None)
    tts = getattr(app, "tts_manager", None)
    process = psutil.Process()
    registry = getattr(brain, "registry", None)
    skills_count = len(registry.list()) if hasattr(registry, "list") else len(registry or ())
    voice_queue = getattr(listener, "_audio_queue", None)
    tts_queue = getattr(tts, "_queue", None)
    return {
        "core_status": getattr(getattr(app, "status", None), "value", "UNKNOWN"),
        "llm_status": (controller.subsystems.get("LLM", "UNKNOWN") if controller else "UNKNOWN"),
        "memory_status": getattr(getattr(brain, "memory", None), "status", "OFFLINE"),
        "context_status": getattr(getattr(brain, "context", None), "status", "OFFLINE"),
        "voice_status": getattr(getattr(listener, "state", None), "value", "OFFLINE"),
        "tts_status": getattr(tts, "state", "OFFLINE"),
        "hud_status": getattr(getattr(controller, "state", None), "value", "OFFLINE"),
        "skills_count": skills_count,
        "threads_count": threading.active_count(),
        "threads": sorted(thread.name for thread in threading.enumerate()),
        "queue_sizes": {
            "voice": voice_queue.qsize() if voice_queue else 0,
            "tts": tts_queue.qsize() if tts_queue else 0,
            "workers": controller.thread_pool.activeThreadCount() if controller else 0,
        },
        "cpu_percent": process.cpu_percent(interval=None),
        "ram_mb": process.memory_info().rss / (1024 * 1024),
    }
