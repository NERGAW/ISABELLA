"""User-controlled device trust Skills."""

from .base import ParameterSpec, RiskLevel, SkillDefinition, SkillResult


def create_node_security_skills(nodes, devices):
    def start(_):
        data = devices.start_pairing()
        return SkillResult(True, "nodes.start_pairing", "Janela de pareamento aberta por tempo limitado.", data)

    def list_trusted(_):
        items = [{"node_id": item.node_id, "permissions": list(item.permissions), "created_at": item.created_at}
                 for item in devices.store.list() if item.trust_status.value == "TRUSTED"]
        return SkillResult(True, "nodes.list_trusted", f"{len(items)} dispositivo(s) confiável(is).", {"devices": items})

    def revoke(args):
        nodes.revoke(args["node_id"])
        return SkillResult(True, "nodes.revoke", "Dispositivo revogado.", {"node_id": args["node_id"]})

    return [
        SkillDefinition("nodes.start_pairing", "Parear dispositivo", "Abre uma janela temporária de pareamento.", "nodes", {}, RiskLevel.CAUTION, start),
        SkillDefinition("nodes.list_trusted", "Dispositivos confiáveis", "Lista dispositivos aprovados.", "nodes", {}, RiskLevel.SAFE, list_trusted),
        SkillDefinition("nodes.revoke", "Revogar dispositivo", "Revoga permanentemente a credencial atual.", "nodes", {"node_id": ParameterSpec(str)}, RiskLevel.CRITICAL, revoke),
    ]
