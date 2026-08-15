"""Strict wake-word normalization and command extraction."""

import re
import unicodedata

from .models import VoiceCommand


def normalize_transcription(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold().strip()
    normalized = re.sub(r"[^\w\sÀ-ÿ'-]", " ", normalized, flags=re.UNICODE)
    return re.sub(r"\s+", " ", normalized).strip()


class WakeWordDetector:
    def __init__(self, aliases: list[str]) -> None:
        normalized_aliases = [normalize_transcription(alias) for alias in aliases]
        escaped = "|".join(re.escape(alias) for alias in sorted(normalized_aliases, key=len, reverse=True))
        self._pattern = re.compile(rf"^(?:ei\s+)?(?P<alias>{escaped})\b\s*(?P<command>.*)$", re.IGNORECASE)

    def detect(self, text: str) -> VoiceCommand:
        normalized = normalize_transcription(text)
        match = self._pattern.match(normalized)
        if not match:
            return VoiceCommand(text, normalized, False, "")
        return VoiceCommand(
            raw_text=text,
            normalized_text=normalized,
            wake_word_detected=True,
            command_text=match.group("command").strip(),
            wake_alias=match.group("alias").lower(),
        )
