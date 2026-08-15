"""Authorized Skill System for ISABELLA."""

from .applications import ApplicationResolver, create_application_skills
from .base import ParameterSpec, RiskLevel, SkillDefinition, SkillResult
from .browser import create_browser_skills
from .registry import SkillRegistry
from .system import create_system_skills


def build_default_registry() -> SkillRegistry:
    registry = SkillRegistry()
    for definition in (
        create_application_skills()
        + create_browser_skills()
        + create_system_skills()
    ):
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
]
