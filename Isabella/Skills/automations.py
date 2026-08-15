"""Authorized CRUD Skills for the Automations Engine."""

from typing import Any

from .base import ParameterSpec, RiskLevel, SkillDefinition, SkillResult


def create_automation_skills(manager) -> list[SkillDefinition]:
    def create(arguments: dict[str, Any]) -> SkillResult:
        automation = manager.create_automation(arguments["specification"])
        return SkillResult(True, "automations.create", "Automação criada.", {"automation": automation.to_dict()})

    def list_items(arguments: dict[str, Any]) -> SkillResult:
        items = [item.to_dict() for item in manager.list()]
        return SkillResult(True, "automations.list", f"{len(items)} automação(ões).", {"automations": items})

    def enable(arguments: dict[str, Any]) -> SkillResult:
        item = manager.enable(arguments["id"])
        return SkillResult(True, "automations.enable", "Automação habilitada.", {"automation": item.to_dict()})

    def disable(arguments: dict[str, Any]) -> SkillResult:
        item = manager.disable(arguments["id"])
        return SkillResult(True, "automations.disable", "Automação desabilitada.", {"automation": item.to_dict()})

    def delete(arguments: dict[str, Any]) -> SkillResult:
        manager.delete(arguments["id"])
        return SkillResult(True, "automations.delete", "Automação removida.")

    item_id = {"id": ParameterSpec(str)}
    return [
        # Creation is conservatively CRITICAL because its future action set is dynamic.
        SkillDefinition("automations.create", "Criar automação", "Cria uma regra somente após confirmação explícita.", "automations", {"specification": ParameterSpec(dict)}, RiskLevel.CRITICAL, create),
        SkillDefinition("automations.list", "Listar automações", "Lista regras persistidas.", "automations", {}, RiskLevel.SAFE, list_items),
        SkillDefinition("automations.enable", "Habilitar automação", "Habilita uma regra existente.", "automations", item_id, RiskLevel.CAUTION, enable),
        SkillDefinition("automations.disable", "Desabilitar automação", "Desabilita uma regra existente.", "automations", item_id, RiskLevel.SAFE, disable),
        SkillDefinition("automations.delete", "Excluir automação", "Exclui uma regra persistida.", "automations", item_id, RiskLevel.CAUTION, delete),
    ]

