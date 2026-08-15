"""Configurable search provider contracts and public DuckDuckGo adapter."""

from __future__ import annotations

from html.parser import HTMLParser
import os
from typing import Protocol
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests

from .models import SearchHit


class SearchUnavailableError(RuntimeError):
    pass


class SearchProvider(Protocol):
    def search(self, query: str, limit: int) -> list[SearchHit]: ...

    def health_check(self) -> bool: ...


class _DuckDuckGoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hits: list[SearchHit] = []
        self._url = ""
        self._title: list[str] = []
        self._snippet: list[str] = []
        self._capture: str | None = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = attributes.get("class", "").split()
        if tag == "a" and "result__a" in classes:
            self._flush()
            self._url = attributes.get("href", "")
            self._capture = "title"
        elif tag in {"a", "div"} and "result__snippet" in classes and self._url:
            self._capture = "snippet"

    def handle_endtag(self, tag):
        if tag in {"a", "div"}:
            self._capture = None

    def handle_data(self, data):
        if self._capture == "title":
            self._title.append(data)
        elif self._capture == "snippet":
            self._snippet.append(data)

    def close(self):
        super().close()
        self._flush()

    def _flush(self):
        if not self._url:
            return
        url = urljoin("https://duckduckgo.com", self._url)
        redirect = parse_qs(urlparse(url).query).get("uddg")
        if redirect:
            url = unquote(redirect[0])
        title = " ".join("".join(self._title).split())
        snippet = " ".join("".join(self._snippet).split())
        if title and url.startswith(("http://", "https://")):
            self.hits.append(SearchHit(title, url, snippet))
        self._url = ""
        self._title = []
        self._snippet = []


class DuckDuckGoHTMLProvider:
    def __init__(self, endpoint: str, timeout: float, user_agent: str, session=None) -> None:
        self.endpoint = endpoint
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent})

    def search(self, query: str, limit: int) -> list[SearchHit]:
        try:
            response = self.session.post(self.endpoint, data={"q": query}, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise SearchUnavailableError("Search provider is unavailable") from exc
        parser = _DuckDuckGoParser()
        parser.feed(response.text)
        parser.close()
        return parser.hits[:limit]

    def health_check(self) -> bool:
        parsed = urlparse(self.endpoint)
        return parsed.scheme == "https" and bool(parsed.hostname)


def build_search_provider(config: dict, session=None) -> SearchProvider:
    provider = config["provider"]
    if provider == "duckduckgo_html":
        return DuckDuckGoHTMLProvider(
            config["search_endpoint"], float(config["timeout_seconds"]),
            config["user_agent"], session=session,
        )
    key_variable = config.get("api_key_environment_variable")
    if key_variable and not os.getenv(key_variable):
        raise SearchUnavailableError(f"Missing provider credential environment variable: {key_variable}")
    raise SearchUnavailableError(f"Unsupported search provider: {provider}")

