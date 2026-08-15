"""Immutable web research records and citations."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str = ""


@dataclass(frozen=True)
class Source:
    title: str
    url: str
    domain: str
    retrieved_at: str
    snippet: str
    content: str = field(default="", repr=False)

    @classmethod
    def create(cls, title: str, url: str, snippet: str, content: str) -> "Source":
        return cls(title or url, url, (urlparse(url).hostname or "").lower(), utc_now(), snippet, content)

    def citation(self) -> dict[str, str]:
        return {
            "title": self.title, "url": self.url, "domain": self.domain,
            "retrieved_at": self.retrieved_at, "snippet": self.snippet,
        }


@dataclass(frozen=True)
class ResearchResult:
    query: str
    answer: str
    sources: tuple[Source, ...] = ()
    cached: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query, "answer": self.answer,
            "sources": [source.citation() for source in self.sources],
            "cached": self.cached, "error": self.error,
        }

