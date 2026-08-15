"""Allowlisted, explicit and on-demand Vision Skills."""

from Isabella.Vision.manager import VisionManager
from .base import RiskLevel, SkillDefinition, SkillResult


def create_vision_skills(manager: VisionManager) -> list[SkillDefinition]:
    def execute(skill_id, operation):
        def capture(arguments):
            result = operation()
            data = {}
            if result.capture:
                data = {
                    "path": str(result.capture.path) if result.capture.path else None,
                    "width": result.capture.width,
                    "height": result.capture.height,
                    "source": result.capture.source.value,
                    "timestamp": result.capture.timestamp,
                    "active_window": result.capture.active_window,
                    "temporary": result.capture.temporary,
                }
            return SkillResult(
                result.success, skill_id, result.message, data,
                error_code=result.error_code, status="completed" if result.success else "failed",
            )
        return capture

    return [
        SkillDefinition(
            "vision.capture_screen", "Capturar tela", "Captura a tela principal sob demanda.",
            "vision", {}, RiskLevel.SAFE, execute("vision.capture_screen", manager.capture_screen),
        ),
        SkillDefinition(
            "vision.capture_active_window", "Capturar janela ativa", "Captura somente a janela ativa.",
            "vision", {}, RiskLevel.SAFE, execute("vision.capture_active_window", manager.capture_active_window),
        ),
        SkillDefinition(
            "vision.capture_camera", "Capturar câmera", "Captura um frame da câmera somente após pedido explícito.",
            "vision", {}, RiskLevel.SAFE, execute("vision.capture_camera", manager.capture_camera),
        ),
    ]
