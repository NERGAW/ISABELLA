"""Authorized Windows application discovery and lifecycle skills."""

import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any
import winreg

import psutil

from Isabella.Core.config import PROJECT_ROOT
from .base import ParameterSpec, RiskLevel, SkillDefinition, SkillResult


DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "applications.json"


class ApplicationResolver:
    def __init__(self, config_path: Path | None = None) -> None:
        self.config_path = config_path or DEFAULT_CONFIG_PATH
        with self.config_path.open(encoding="utf-8") as config_file:
            self.applications: dict[str, dict[str, Any]] = json.load(config_file)

    def normalize(self, name: str) -> str | None:
        normalized = name.strip().lower()
        for app_id, config in self.applications.items():
            aliases = [str(alias).lower() for alias in config.get("aliases", [])]
            if normalized == app_id or normalized in aliases:
                return app_id
        return None

    def resolve(self, name: str) -> Path | str | None:
        app_id = self.normalize(name)
        if app_id is None:
            return None
        config = self.applications[app_id]

        for configured_path in config.get("paths", []):
            path = Path(os.path.expandvars(configured_path)).expanduser()
            if path.exists():
                return path

        executables = [str(item) for item in config.get("executables", [])]
        for executable in executables:
            found = shutil.which(executable)
            if found:
                return Path(found)

        standard = self._standard_locations(app_id, executables)
        if standard:
            return standard
        shortcut = self._start_menu_shortcut(config.get("aliases", []))
        if shortcut:
            return shortcut
        app_path = self._app_paths(executables)
        return app_path or self._store_app(config.get("aliases", []))

    @staticmethod
    def _standard_locations(app_id: str, executables: list[str]) -> Path | None:
        roots = [
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
            os.environ.get("LOCALAPPDATA"),
        ]
        relative: dict[str, list[str]] = {
            "chrome": [r"Google\Chrome\Application\chrome.exe"],
            "discord": [r"Discord\app-*\Discord.exe", r"Discord\Update.exe"],
            "steam": [r"Steam\steam.exe"],
            "whatsapp": [r"WhatsApp\WhatsApp.exe"],
            "vscode": [r"Programs\Microsoft VS Code\Code.exe"],
        }
        for root in filter(None, roots):
            for candidate in relative.get(app_id, []):
                matches = list(Path(root).glob(candidate))
                if matches:
                    return sorted(matches)[-1]
            for executable in executables:
                direct = Path(root) / executable
                if direct.exists():
                    return direct
        return None

    @staticmethod
    def _start_menu_shortcut(aliases: list[str]) -> Path | None:
        roots = [
            Path(os.environ[variable]) / relative
            for variable, relative in (
                ("APPDATA", r"Microsoft\Windows\Start Menu\Programs"),
                ("PROGRAMDATA", r"Microsoft\Windows\Start Menu\Programs"),
            )
            if os.environ.get(variable)
        ]
        wanted = [alias.lower() for alias in aliases]
        for root in roots:
            if not root.exists():
                continue
            for shortcut in root.rglob("*.lnk"):
                if any(alias in shortcut.stem.lower() for alias in wanted):
                    return shortcut
        return None

    @staticmethod
    def _app_paths(executables: list[str]) -> Path | None:
        key_roots = (
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\App Paths"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\App Paths"),
        )
        for executable in executables:
            for root, key_path in key_roots:
                try:
                    with winreg.OpenKey(root, f"{key_path}\\{executable}") as key:
                        value, _ = winreg.QueryValueEx(key, None)
                    path = Path(value)
                    if path.exists():
                        return path
                except OSError:
                    continue
        return None

    @staticmethod
    def _store_app(aliases: list[str]) -> str | None:
        command = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress",
        ]
        try:
            completed = subprocess.run(command, check=False, capture_output=True, timeout=5)
            entries = None
            for encoding in ("utf-8-sig", "utf-16", "cp1252"):
                try:
                    entries = json.loads(completed.stdout.decode(encoding))
                    break
                except (UnicodeError, json.JSONDecodeError):
                    continue
            if entries is None:
                return None
        except (OSError, subprocess.SubprocessError):
            return None
        if isinstance(entries, dict):
            entries = [entries]
        wanted = [alias.lower() for alias in aliases]
        for entry in entries:
            display_name = str(entry.get("Name", "")).lower()
            if any(alias in display_name for alias in wanted):
                return f"shell:AppsFolder\\{entry['AppID']}"
        return None

    def process_names(self, name: str) -> set[str]:
        app_id = self.normalize(name)
        if app_id is None:
            return set()
        return {Path(item).stem.lower() for item in self.applications[app_id].get("executables", [])}


def create_application_skills(resolver: ApplicationResolver | None = None) -> list[SkillDefinition]:
    app_resolver = resolver or ApplicationResolver()

    def open_application(arguments: dict[str, Any]) -> SkillResult:
        requested = arguments["name"]
        app_id = app_resolver.normalize(requested)
        path = app_resolver.resolve(requested)
        if app_id is None:
            return SkillResult(False, "applications.open", f"Aplicativo desconhecido: {requested}.", error_code="UNKNOWN_APPLICATION", status="failed")
        if path is None:
            return SkillResult(False, "applications.open", f"Não foi possível localizar {app_id}.", error_code="APP_NOT_FOUND", status="failed")
        os.startfile(str(path))
        return SkillResult(True, "applications.open", f"{app_id.title()} aberto.", {"application": app_id, "path": str(path)})

    def close_application(arguments: dict[str, Any]) -> SkillResult:
        requested = arguments["name"]
        app_id = app_resolver.normalize(requested)
        names = app_resolver.process_names(requested)
        if app_id is None:
            return SkillResult(False, "applications.close", f"Aplicativo desconhecido: {requested}.", error_code="UNKNOWN_APPLICATION", status="failed")
        processes = [process for process in psutil.process_iter(["name"]) if (process.info["name"] or "").lower().removesuffix(".exe") in names]
        if not processes:
            return SkillResult(False, "applications.close", f"{app_id.title()} não está em execução.", error_code="APP_NOT_RUNNING", status="failed")
        for process in processes:
            process.terminate()
        _, alive = psutil.wait_procs(processes, timeout=5)
        if alive:
            return SkillResult(False, "applications.close", f"{app_id.title()} não respondeu ao encerramento normal.", {"remaining": len(alive)}, "CLOSE_TIMEOUT", "failed")
        return SkillResult(True, "applications.close", f"{app_id.title()} fechado.", {"processes": len(processes)})

    parameters = {"name": ParameterSpec(str)}
    return [
        SkillDefinition("applications.open", "Abrir aplicativo", "Abre um aplicativo autorizado.", "applications", parameters, RiskLevel.SAFE, open_application),
        SkillDefinition("applications.close", "Fechar aplicativo", "Solicita encerramento normal de um aplicativo.", "applications", parameters, RiskLevel.CAUTION, close_application),
    ]
