from Isabella.Agents.base import Agent


def build_specialized_agents():
    return (
        Agent("SYSTEM_AGENT", "Windows, aplicativos, estado e diagnóstico.", ("system", "applications", "diagnostics"), ("system.*", "applications.*", "browser.*"), ("system_state",)),
        Agent("RESEARCH_AGENT", "Pesquisa web, avaliação de fontes e citações.", ("research", "sources", "citations"), (), ("current_mode",)),
        Agent("VISION_AGENT", "Compreensão de tela, câmera e contexto visual.", ("screen", "camera", "visual_context"), ("vision.*",), ("last_screen_summary",)),
        Agent("MEMORY_AGENT", "Recall, preferências e contexto de projeto.", ("recall", "preferences", "project_context"), (), ("current_project",)),
        Agent("ENGINEERING_AGENT", "Projeto, logs, Git e diagnóstico técnico.", ("engineering", "logs", "git", "technical_diagnostics"), ("applications.*", "system.diagnostics", "browser.*"), ("current_project", "active_application")),
    )
