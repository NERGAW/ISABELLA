"""Specific, allowlisted Home Skills; deliberately no raw command Skill."""

from .base import ParameterSpec, RiskLevel, SkillDefinition, SkillResult


def create_home_skills(manager):
    device = {"device_id": ParameterSpec(str)}
    def run(skill_id, command, args):
        try: return SkillResult(True, skill_id, "Comando Home concluído.", manager.command(args["device_id"], command))
        except (PermissionError, ConnectionError, ValueError) as exc: return SkillResult(False, skill_id, str(exc), error_code="HOME_COMMAND_DENIED", status="denied")
    return [
        SkillDefinition("home.light_on", "Ligar luz", "Liga somente luz cadastrada.", "home", device, RiskLevel.CAUTION, lambda a: run("home.light_on", "light_on", a)),
        SkillDefinition("home.light_off", "Desligar luz", "Desliga somente luz cadastrada.", "home", device, RiskLevel.SAFE, lambda a: run("home.light_off", "light_off", a)),
        SkillDefinition("home.get_temperature", "Consultar temperatura", "Consulta sensor cadastrado.", "home", device, RiskLevel.SAFE, lambda a: run("home.get_temperature", "get_temperature", a)),
        SkillDefinition("home.get_device_status", "Status Home", "Consulta dispositivo cadastrado.", "home", device, RiskLevel.SAFE, lambda a: run("home.get_device_status", "get_status", a)),
    ]
