"""Authorized Skill System for ISABELLA."""

from .applications import ApplicationResolver, create_application_skills
from .base import ParameterSpec, RiskLevel, SkillDefinition, SkillResult
from .browser import create_browser_skills
from .registry import SkillRegistry
from .system import create_system_skills
from .vision import create_vision_skills
from .diagnostics import create_diagnostics_skill
from .automations import create_automation_skills
from .scheduler import create_scheduler_skills


def build_default_registry(vision_manager=None, event_bus=None, policy_engine=None) -> SkillRegistry:
    registry = SkillRegistry(event_bus=event_bus, policy_engine=policy_engine)
    definitions = (
        create_application_skills()
        + create_browser_skills()
        + create_system_skills()
    )
    if vision_manager is not None:
        definitions += create_vision_skills(vision_manager)
    for definition in definitions:
        registry.register(definition)
    return registry


__all__ = [
    "ApplicationResolver",
    "ParameterSpec",
    "RiskLevel",
    "SkillDefinition",
    "SkillRegistry",
    "SkillResult",
    "build_default_registry",
    "create_vision_skills",
    "create_diagnostics_skill",
    "create_automation_skills",
    "create_scheduler_skills",
]
