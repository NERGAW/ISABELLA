"""Primary Node identity persistence and real capability detection."""

from __future__ import annotations

import json
from pathlib import Path
import platform
import uuid

from Isabella.Protocol import PROTOCOL_VERSION
from .models import Node, NodeStatus, NodeType, TrustState, now_iso


def load_or_create_node_id(path: Path) -> str:
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        node_id = data.get("node_id")
        if isinstance(node_id, str) and node_id.startswith("primary.") and len(node_id) == 40:
            return node_id
        raise ValueError("Persisted primary Node identity is invalid")
    path.parent.mkdir(parents=True, exist_ok=True)
    node_id = f"primary.{uuid.uuid4().hex}"
    path.write_text(json.dumps({"node_id": node_id}, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return node_id


def detect_primary_capabilities(*, app=None, brain=None, controller=None) -> tuple[str, ...]:
    capabilities = {"text_input"}
    if getattr(app, "voice_listener", None) is not None:
        capabilities.add("voice_input")
    if getattr(app, "tts_manager", None) is not None:
        capabilities.add("voice_output")
    vision = getattr(brain, "vision", None)
    if vision:
        health = vision.health_check(check_camera=True)
        if health.get("screen"):
            capabilities.add("screen_capture")
        if health.get("camera"):
            capabilities.add("camera_capture")
    llm = getattr(brain, "llm", None)
    if llm and getattr(llm, "health_check", lambda: False)():
        capabilities.add("local_llm")
    if getattr(brain, "registry", None):
        capabilities.add("skill_execution")
    if controller is not None:
        capabilities.add("hud")
    if getattr(brain, "memory", None) is not None:
        capabilities.add("memory")
    research = getattr(brain, "research", None)
    if research and research.health_check().get("provider_configured"):
        capabilities.add("research")
    return tuple(sorted(capabilities))


def create_primary_node(identity_path: Path, *, app=None, brain=None, controller=None) -> Node:
    now = now_iso()
    return Node(load_or_create_node_id(identity_path), platform.node() or "ISABELLA Primary PC", NodeType.PRIMARY_PC, NodeStatus.ONLINE, PROTOCOL_VERSION, detect_primary_capabilities(app=app, brain=brain, controller=controller), now, now, {"platform": "Windows"}, TrustState.TRUSTED)

