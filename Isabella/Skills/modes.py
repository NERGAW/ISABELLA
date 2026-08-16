from Isabella.Skills.base import ParameterSpec, RiskLevel, SkillDefinition, SkillResult


def create_mode_skill(manager):
    def execute(arguments):
        try:
            mode = manager.set_mode(arguments["mode"])
            return SkillResult(True, "system.set_mode", f"Modo {mode.name} ativado.", {"mode": mode.id})
        except ValueError as exc:
            return SkillResult(False, "system.set_mode", str(exc), error_code="INVALID_MODE", status="rejected")
    return SkillDefinition(
        "system.set_mode", "Alterar modo operacional", "Aplica um perfil operacional sem reduzir a segurança.",
        "system", {"mode": ParameterSpec(str)}, RiskLevel.SAFE, execute,
    )
