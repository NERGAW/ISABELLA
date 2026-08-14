"""Fast hybrid intent router for text requests."""

import logging
import re
from time import perf_counter

from .models import Intent, SkillRequest


LOGGER = logging.getLogger("ROUTER")
ACTION_WORDS = ("abra", "abrir", "inicie", "iniciar", "feche", "fechar", "tire", "captura", "volume", "deslig", "reinici", "suspend")


class Router:
    def __init__(self) -> None:
        self.latencies_ms: list[float] = []

    def route(self, text: str) -> Intent:
        started = perf_counter()
        normalized = text.strip().lower()
        action_count = sum(normalized.count(word) for word in ACTION_WORDS)
        connectors = bool(re.search(r"\b(e depois|depois|e então|,\s*(?:e\s*)?)\b", normalized))
        if action_count >= 2 or (action_count >= 1 and connectors and self._has_two_targets(normalized)):
            intent = Intent.MULTI_STEP
        elif action_count >= 1 or "quero usar o navegador" in normalized:
            intent = Intent.SINGLE_SKILL
        else:
            intent = Intent.CONVERSATION
        latency = (perf_counter() - started) * 1000
        self.latencies_ms.append(latency)
        LOGGER.info("input=%r intent=%s latency_ms=%.3f", text, intent.value, latency)
        return intent

    @staticmethod
    def _has_two_targets(text: str) -> bool:
        targets = ("chrome", "discord", "youtube", "navegador", "captura", "tela")
        return sum(target in text for target in targets) >= 2

    def skill_request(self, text: str) -> SkillRequest:
        normalized = text.lower()
        if "captura" in normalized or "screenshot" in normalized:
            return SkillRequest("system.screenshot", {})
        if "volume" in normalized:
            match = re.search(r"(\d{1,3})", normalized)
            return SkillRequest("system.set_volume", {"level": int(match.group(1)) if match else 50})
        for action, skill in (
            ("deslig", "system.shutdown"),
            ("reinici", "system.restart"),
            ("suspend", "system.sleep"),
        ):
            if action in normalized:
                return SkillRequest(skill, {})
        if "youtube" in normalized:
            return SkillRequest("browser.open_url", {"url": "https://youtube.com"})
        if "discord" in normalized:
            name = "discord"
        else:
            name = "chrome"
        skill = "applications.close" if any(word in normalized for word in ("feche", "fechar")) else "applications.open"
        return SkillRequest(skill, {"name": name})

    @property
    def average_latency_ms(self) -> float:
        return sum(self.latencies_ms) / len(self.latencies_ms) if self.latencies_ms else 0.0
