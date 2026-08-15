"""Allowlisted Windows system skills."""

from datetime import datetime
import subprocess
from typing import Any

from Isabella.Core.config import PROJECT_ROOT
from Isabella.Vision.screen import ScreenCapturer
from .base import ParameterSpec, RiskLevel, SkillDefinition, SkillResult


SCREENSHOT_DIRECTORY = PROJECT_ROOT / "data" / "screenshots"


def create_system_skills(
    screenshot_grabber=None,
    command_runner=subprocess.run,
    screen_capturer=None,
) -> list[SkillDefinition]:
    capturer = screen_capturer or ScreenCapturer(grabber=screenshot_grabber)

    def screenshot(arguments: dict[str, Any]) -> SkillResult:
        SCREENSHOT_DIRECTORY.mkdir(parents=True, exist_ok=True)
        destination = SCREENSHOT_DIRECTORY / f"screenshot_{datetime.now():%Y%m%d_%H%M%S}.png"
        capture = capturer.capture_screen(destination=destination, temporary=False)
        return SkillResult(
            True, "system.screenshot", "Captura de tela salva.",
            {"path": str(destination), "width": capture.width, "height": capture.height},
        )

    def set_volume(arguments: dict[str, Any]) -> SkillResult:
        value = arguments["value"]
        if not 0 <= value <= 100:
            return SkillResult(False, "system.set_volume", "O volume deve estar entre 0 e 100.", error_code="VOLUME_OUT_OF_RANGE", status="failed")
        from pycaw.pycaw import AudioUtilities

        device = AudioUtilities.GetSpeakers()
        device.volume_percent = value
        return SkillResult(True, "system.set_volume", f"Volume ajustado para {value}%.", {"value": value})

    def run_fixed(skill_id: str, command: list[str], message: str) -> SkillResult:
        completed = command_runner(command, check=False, capture_output=True, text=True)
        if completed.returncode != 0:
            return SkillResult(False, skill_id, "O Windows recusou a ação.", error_code="WINDOWS_ACTION_FAILED", status="failed")
        return SkillResult(True, skill_id, message)

    def sleep(arguments: dict[str, Any]) -> SkillResult:
        return run_fixed("system.sleep", ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], "Suspensão iniciada.")

    def shutdown(arguments: dict[str, Any]) -> SkillResult:
        return run_fixed("system.shutdown", ["shutdown.exe", "/s", "/t", "0"], "Desligamento iniciado.")

    def restart(arguments: dict[str, Any]) -> SkillResult:
        return run_fixed("system.restart", ["shutdown.exe", "/r", "/t", "0"], "Reinicialização iniciada.")

    def shutdown_timer(arguments: dict[str, Any]) -> SkillResult:
        minutes = arguments["minutes"]
        if minutes <= 0:
            return SkillResult(False, "system.shutdown_timer", "Os minutos devem ser maiores que zero.", error_code="INVALID_TIMER", status="failed")
        return run_fixed("system.shutdown_timer", ["shutdown.exe", "/s", "/t", str(minutes * 60)], f"Desligamento agendado para {minutes} minuto(s).")

    no_parameters: dict[str, ParameterSpec] = {}
    return [
        SkillDefinition("system.screenshot", "Captura de tela", "Salva uma captura da tela.", "system", no_parameters, RiskLevel.SAFE, screenshot),
        SkillDefinition("system.set_volume", "Ajustar volume", "Ajusta o volume principal entre 0 e 100.", "system", {"value": ParameterSpec(int)}, RiskLevel.CAUTION, set_volume),
        SkillDefinition("system.sleep", "Suspender", "Suspende o Windows após confirmação.", "system", no_parameters, RiskLevel.CRITICAL, sleep),
        SkillDefinition("system.shutdown", "Desligar", "Desliga o Windows após confirmação.", "system", no_parameters, RiskLevel.CRITICAL, shutdown),
        SkillDefinition("system.restart", "Reiniciar", "Reinicia o Windows após confirmação.", "system", no_parameters, RiskLevel.CRITICAL, restart),
        SkillDefinition("system.shutdown_timer", "Agendar desligamento", "Agenda desligamento após confirmação.", "system", {"minutes": ParameterSpec(int)}, RiskLevel.CRITICAL, shutdown_timer),
    ]
