"""Fast hybrid intent router for text requests."""

import logging
import re
from collections import deque
from time import perf_counter
import unicodedata

from .models import Intent, SkillRequest


LOGGER = logging.getLogger("ROUTER")
ACTION_WORDS = (
    "abra", "abre", "abrir", "inicie", "iniciar", "entrar", "usar", "assistir",
    "feche", "fechar", "tire", "captura", "volume", "deslig", "reinici", "suspend",
)


class Router:
    def __init__(self) -> None:
        self.latencies_ms: deque[float] = deque(maxlen=200)

    def route(self, text: str) -> Intent:
        started = perf_counter()
        normalized = self.normalize_text(text)
        clauses = [part.strip() for part in re.split(r"\b(?:e depois|depois|e entao|e)\b|,", normalized) if part.strip()]
        action_clauses = sum(self._is_action(clause) for clause in clauses)
        if action_clauses >= 2:
            intent = Intent.MULTI_STEP
        elif self._is_action(normalized):
            intent = Intent.SINGLE_SKILL
        else:
            intent = Intent.CONVERSATION
        latency = (perf_counter() - started) * 1000
        self.latencies_ms.append(latency)
        LOGGER.info("input=%r intent=%s latency_ms=%.3f", text, intent.value, latency)
        return intent

    @staticmethod
    def _is_action(text: str) -> bool:
        return text.startswith(("http://", "https://")) or any(word in text for word in ACTION_WORDS) or any(
            target in text for target in ("discord", "youtube", "github", "editor de codigo")
        )

    def skill_request(self, text: str) -> SkillRequest:
        normalized = self.normalize_text(text)
        url_match = re.search(r"https?://[^\s]+", normalized)
        if url_match:
            return SkillRequest("browser.open_url", {"url": url_match.group(0).rstrip(".,!?")})
        if "captura" in normalized or "screenshot" in normalized:
            return SkillRequest("system.screenshot", {})
        if "volume" in normalized:
            match = re.search(r"(\d{1,3})", normalized)
            value = int(match.group(1)) if match else (50 if "metade" in normalized else -1)
            return SkillRequest("system.set_volume", {"value": value})
        timer = re.search(r"(?:deslig\w*).+?(\d+)\s*min", normalized)
        if timer:
            return SkillRequest("system.shutdown_timer", {"minutes": int(timer.group(1))})
        for action, skill in (
            ("deslig", "system.shutdown"),
            ("reinici", "system.restart"),
            ("suspend", "system.sleep"),
        ):
            if action in normalized:
                return SkillRequest(skill, {})
        for target in ("youtube", "github", "google"):
            if target in normalized:
                return SkillRequest("browser.open_url", {"target": target})
        aliases = (
            (("discord",), "discord"),
            (("steam",), "steam"),
            (("whatsapp", "whats app"), "whatsapp"),
            (("vs code", "vscode", "visual studio code", "editor de codigo"), "vscode"),
            (("bloco de notas", "notepad"), "notepad"),
            (("calculadora", "calculator", "calc"), "calculator"),
            (("explorador de arquivos", "explorer"), "explorer"),
            (("chrome", "navegador"), "chrome"),
        )
        name = next((app_id for names, app_id in aliases if any(alias in normalized for alias in names)), None)
        if name is None:
            match = re.search(r"(?:abra|abre|abrir|inicie|iniciar)\s+(?:o|a)?\s*(.+?)(?:\s+para mim)?[.!?]*$", normalized)
            name = match.group(1).strip() if match else normalized
        skill = "applications.close" if any(word in normalized for word in ("feche", "fechar")) else "applications.open"
        return SkillRequest(skill, {"name": name})

    @staticmethod
    def normalize_text(text: str) -> str:
        decomposed = unicodedata.normalize("NFKD", text.strip().lower())
        return "".join(character for character in decomposed if not unicodedata.combining(character))

    @property
    def average_latency_ms(self) -> float:
        return sum(self.latencies_ms) / len(self.latencies_ms) if self.latencies_ms else 0.0
