"""Safe browser target and URL opening."""

from typing import Any
from urllib.parse import urlparse
import webbrowser

from .base import ParameterSpec, RiskLevel, SkillDefinition, SkillResult


TARGETS = {
    "youtube": "https://youtube.com",
    "google": "https://google.com",
    "github": "https://github.com",
}


def normalize_url(value: str) -> str | None:
    normalized = value.strip()
    alias = TARGETS.get(normalized.lower())
    if alias:
        return alias
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return normalized


def create_browser_skills(opener=webbrowser.open) -> list[SkillDefinition]:
    def open_url(arguments: dict[str, Any]) -> SkillResult:
        value = arguments.get("url") or arguments.get("target")
        if not value:
            return SkillResult(False, "browser.open_url", "Informe uma URL ou destino.", error_code="MISSING_TARGET", status="failed")
        url = normalize_url(value)
        if url is None:
            return SkillResult(False, "browser.open_url", "URL inválida ou esquema não permitido.", error_code="INVALID_URL", status="failed")
        if not opener(url):
            return SkillResult(False, "browser.open_url", "O navegador não aceitou a solicitação.", error_code="BROWSER_OPEN_FAILED", status="failed")
        return SkillResult(True, "browser.open_url", f"Abrindo {url}.", {"url": url})

    return [
        SkillDefinition(
            "browser.open_url",
            "Abrir URL",
            "Abre somente destinos HTTP ou HTTPS.",
            "browser",
            {"url": ParameterSpec(str, required=False), "target": ParameterSpec(str, required=False)},
            RiskLevel.SAFE,
            open_url,
        )
    ]
