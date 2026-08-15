"""Security-gated Scheduler CRUD and internal reminder Skill."""

from typing import Any

from .base import ParameterSpec, RiskLevel, SkillDefinition, SkillResult


def create_scheduler_skills(manager) -> list[SkillDefinition]:
    def create(arguments: dict[str, Any]) -> SkillResult:
        task = manager.create_task(arguments["specification"])
        return SkillResult(True, "scheduler.create", "Tarefa agendada.", {"task": task.to_dict()})

    def list_tasks(arguments: dict[str, Any]) -> SkillResult:
        tasks = [item.to_dict() for item in manager.list()]
        return SkillResult(True, "scheduler.list", f"{len(tasks)} tarefa(s) agendada(s).", {"tasks": tasks})

    def cancel(arguments: dict[str, Any]) -> SkillResult:
        task = manager.cancel(arguments["id"])
        return SkillResult(True, "scheduler.cancel", "Tarefa cancelada.", {"task": task.to_dict()})

    def pause(arguments: dict[str, Any]) -> SkillResult:
        task = manager.pause(arguments["id"])
        return SkillResult(True, "scheduler.pause", "Tarefa pausada.", {"task": task.to_dict()})

    def resume(arguments: dict[str, Any]) -> SkillResult:
        task = manager.resume(arguments["id"])
        return SkillResult(True, "scheduler.resume", "Tarefa retomada.", {"task": task.to_dict()})

    def reminder(arguments: dict[str, Any]) -> SkillResult:
        manager.notify_reminder(arguments["text"])
        return SkillResult(True, "scheduler.reminder", arguments["text"])

    item_id = {"id": ParameterSpec(str)}
    return [
        # Dynamic future actions make task creation confirmation-worthy.
        SkillDefinition("scheduler.create", "Agendar tarefa", "Cria tarefa temporal após confirmação explícita.", "scheduler", {"specification": ParameterSpec(dict)}, RiskLevel.CRITICAL, create),
        SkillDefinition("scheduler.list", "Listar tarefas", "Lista tarefas temporais.", "scheduler", {}, RiskLevel.SAFE, list_tasks),
        SkillDefinition("scheduler.cancel", "Cancelar tarefa", "Cancela uma tarefa facilmente.", "scheduler", item_id, RiskLevel.SAFE, cancel),
        SkillDefinition("scheduler.pause", "Pausar tarefa", "Pausa uma tarefa.", "scheduler", item_id, RiskLevel.SAFE, pause),
        SkillDefinition("scheduler.resume", "Retomar tarefa", "Retoma uma tarefa pausada.", "scheduler", item_id, RiskLevel.CAUTION, resume),
        SkillDefinition("scheduler.reminder", "Emitir lembrete", "Notifica HUD, TTS e Event Bus.", "scheduler", {"text": ParameterSpec(str)}, RiskLevel.SAFE, reminder),
    ]

