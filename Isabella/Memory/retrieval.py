"""Deterministic parsing and keyword retrieval helpers; no embeddings."""

from __future__ import annotations

import re
import unicodedata

from .models import MemoryType


STOPWORDS = {
    "a", "as", "de", "do", "da", "dos", "das", "e", "eu", "me", "meu", "minha",
    "o", "os", "que", "qual", "sobre", "um", "uma", "voce", "lembra", "lembrar",
}
SECRET_TERMS = (
    "password", "senha", "token", "api key", "apikey", "secret", "segredo",
    "private key", "chave privada", "cartao", "cartão", "credencial",
)


def normalize(text: str) -> str:
    value = unicodedata.normalize("NFKD", text.strip().lower())
    return "".join(character for character in value if not unicodedata.combining(character))


def slugify(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", normalize(text))
    return "_".join(words)[:80]


def contains_secret(text: str) -> bool:
    lowered = normalize(text)
    return any(normalize(term) in lowered for term in SECRET_TERMS)


def keywords(text: str) -> tuple[str, ...]:
    values = [word for word in re.findall(r"[a-z0-9]+", normalize(text)) if len(word) > 2 and word not in STOPWORDS]
    aliases = {"navegador": "browser", "projeto": "project", "preferencia": "preference", "preferido": "preference"}
    values.extend(aliases[word] for word in tuple(values) if word in aliases)
    return tuple(dict.fromkeys(values))[:8]


def parse_remember(text: str) -> tuple[MemoryType, str, str, tuple[str, ...]] | None:
    normalized = normalize(text).rstrip(".!?")
    if not re.match(r"^(lembre|lembra|memorize)\b", normalized):
        return None
    content = re.sub(
        r"^(lembre|lembra|memorize)(-se)?\s+(de\s+)?(que\s+)?", "", text.strip(),
        flags=re.IGNORECASE,
    ).strip().rstrip(".!?")
    browser = re.search(r"(?:meu navegador preferido (?:é|e)|prefiro(?: o navegador)?|navegador que prefiro (?:é|e))\s+(.+)$", content, re.IGNORECASE)
    if browser:
        return MemoryType.PREFERENCE, "preferred_browser", browser.group(1).strip(), ("browser", "preference")
    project = re.search(r"(?:o )?projeto atual (?:se chama|é|e)\s+(.+)$", content, re.IGNORECASE)
    if project:
        return MemoryType.PROJECT, "current_project_name", project.group(1).strip(), ("project", "projeto")
    match = re.match(r"(.+?)\s+(?:é|e|eh)\s+(.+)$", content, re.IGNORECASE)
    if match:
        key_text, value = match.groups()
        return MemoryType.FACT, slugify(key_text), value.strip(), keywords(key_text)
    if content:
        return MemoryType.EPISODIC, slugify(content[:50]), content, keywords(content)
    return None


def preferred_browser_query(text: str) -> bool:
    value = normalize(text)
    return "navegador" in value and any(term in value for term in ("prefiro", "preferido", "qual"))


def browser_forget_query(text: str) -> bool:
    value = normalize(text)
    return value.startswith(("esqueca", "apague")) and "navegador" in value


def working_topic_query(text: str) -> bool:
    value = normalize(text)
    return "qual" in value and any(term in value for term in ("planeta", "assunto", "discutindo", "falando"))
