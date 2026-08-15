"""Source normalization and untrusted-content boundaries."""

from __future__ import annotations

import re

from .models import Source


INJECTION_PATTERNS = (
    r"ignore (?:all |any )?(?:previous|prior|system) instructions?[^.\n]*",
    r"disregard (?:all |any )?(?:previous|prior|system) instructions?[^.\n]*",
    r"(?:reveal|show|print) (?:the )?(?:system prompt|developer message)[^.\n]*",
    r"(?:execute|call|invoke) (?:a |the )?(?:tool|command|permission)[^.\n]*",
)


def sanitize_untrusted_text(text: str, maximum: int) -> str:
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text)
    for pattern in INJECTION_PATTERNS:
        cleaned = re.sub(pattern, "[conteúdo instrucional não confiável removido]", cleaned, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", cleaned).strip()[:maximum]


def build_untrusted_context(sources: list[Source] | tuple[Source, ...]) -> str:
    blocks = []
    for index, source in enumerate(sources, 1):
        blocks.append(
            f"[FONTE {index} — DADOS WEB NÃO CONFIÁVEIS]\n"
            f"Título: {source.title}\nURL: {source.url}\nConteúdo: {source.content}\n"
            f"[FIM DA FONTE {index}]"
        )
    return "\n\n".join(blocks)

