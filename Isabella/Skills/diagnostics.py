"""Safe explicit diagnostics Skill."""

from typing import Any

from .base import ParameterSpec, RiskLevel, SkillDefinition, SkillResult


def create_diagnostics_skill(manager) -> SkillDefinition:
    def execute(arguments: dict[str, Any]) -> SkillResult:
        detailed = bool(arguments.get("detailed", False))
        report = manager.check(detailed=detailed, expensive=True)
        return SkillResult(
            True, "system.diagnostics", report.summary,
            {"report": report.to_dict(), "detailed": detailed},
        )

    return SkillDefinition(
        "system.diagnostics", "Diagnóstico do sistema",
        "Consulta a saúde técnica da I.S.A.B.E.L.L.A. sem alterar o sistema.",
        "system", {"detailed": ParameterSpec(bool, required=False)},
        RiskLevel.SAFE, execute,
    )

