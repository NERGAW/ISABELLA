"""Deterministic declarative composite generator; it never writes Python code."""

from __future__ import annotations

from typing import Any

from Isabella.Skills.base import RiskLevel
from .models import ForgeInput, ForgeStep, GeneratedTest, SkillCandidate, SkillSpec


RISK_ORDER = {RiskLevel.SAFE: 0, RiskLevel.CAUTION: 1, RiskLevel.CRITICAL: 2}


class SkillGenerator:
    def __init__(self, registry) -> None:
        self.registry = registry

    def create_composite(
        self, *, skill_id: str, name: str, description: str,
        steps: list[dict[str, Any]], inputs: list[dict[str, Any]] | None = None,
        outputs: dict[str, str] | None = None, dependencies: list[str] | None = None,
        permissions: list[str] | None = None, origin: str = "manual_structured",
    ) -> SkillCandidate:
        forge_inputs = tuple(ForgeInput(**item) for item in (inputs or []))
        forge_steps = tuple(
            ForgeStep(str(item["skill_id"]), dict(item.get("arguments", {}))) for item in steps
        )
        risks = [
            definition.risk_level for step in forge_steps
            if (definition := self.registry.get(step.skill_id)) is not None
        ]
        risk = max(risks, key=RISK_ORDER.get) if risks else RiskLevel.CAUTION
        spec = SkillSpec(
            skill_id, name, description, forge_inputs, dict(outputs or {}), risk,
            tuple(dependencies or ()), forge_steps, tuple(permissions or ()),
        )
        example_inputs = {
            item.name: item.example for item in forge_inputs if item.example is not None
        }
        tests = (
            GeneratedTest("generated_dry_run", example_inputs, tuple(step.skill_id for step in forge_steps)),
        )
        return SkillCandidate(spec, tests, origin=origin)

