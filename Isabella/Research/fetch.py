"""Bounded public-page fetching with URL and SSRF protections."""

from __future__ import annotations

from html.parser import HTMLParser
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import requests

from .sources import sanitize_untrusted_text


class UnsafeURLError(ValueError):
    pass


class FetchError(RuntimeError):
    pass


def validate_public_url(url: str, resolver=socket.getaddrinfo) -> str:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError as exc:
        raise UnsafeURLError("Invalid URL") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UnsafeURLError("Only public HTTP(S) URLs are allowed")
    if parsed.username or parsed.password:
        raise UnsafeURLError("Credentials in URLs are not allowed")
    hostname = parsed.hostname.rstrip(".").casefold()
    if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
        raise UnsafeURLError("Local hosts are not allowed")
    try:
        addresses = {item[4][0].split("%")[0] for item in resolver(hostname, port or (443 if parsed.scheme == "https" else 80))}
    except OSError as exc:
        raise UnsafeURLError("URL host could not be resolved") from exc
    if not addresses:
        raise UnsafeURLError("URL host has no address")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise UnsafeURLError("Private or special network addresses are not allowed")
    return url


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.title: list[str] = []
        self._ignored = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg"}:
            self._ignored += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg"} and self._ignored:
            self._ignored -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._ignored:
            return
        self.parts.append(data)
        if self._in_title:
            self.title.append(data)


class WebFetcher:
    def __init__(
        self, timeout: float, maximum_bytes: int, maximum_characters: int,
        user_agent: str, session=None, resolver=socket.getaddrinfo,
    ) -> None:
        self.timeout = timeout
        self.maximum_bytes = maximum_bytes
        self.maximum_characters = maximum_characters
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self.resolver = resolver

    def fetch(self, url: str) -> tuple[str, str, str]:
        current = validate_public_url(url, self.resolver)
        response = None
        for _ in range(4):
            try:
                response = self.session.get(current, timeout=self.timeout, stream=True, allow_redirects=False)
            except requests.RequestException as exc:
                raise FetchError("Source is unavailable") from exc
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location")
                response.close()
                if not location:
                    raise FetchError("Redirect has no destination")
                current = validate_public_url(urljoin(current, location), self.resolver)
                continue
            break
        else:
            raise FetchError("Too many redirects")
        try:
            response.raise_for_status()
        except requests.RequestException as exc:
            raise FetchError("Source returned an error") from exc
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
        if content_type not in {"text/html", "text/plain", "application/xhtml+xml"}:
            response.close()
            raise FetchError("Unsupported source content type")
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_content(16384):
            size += len(chunk)
            if size > self.maximum_bytes:
                response.close()
                raise FetchError("Source is too large")
            chunks.append(chunk)
        encoding = response.encoding or "utf-8"
        response.close()
        raw = b"".join(chunks).decode(encoding, errors="replace")
        if content_type == "text/plain":
            return current, "", sanitize_untrusted_text(raw, self.maximum_characters)
        parser = _TextExtractor()
        parser.feed(raw)
        title = sanitize_untrusted_text(" ".join(parser.title), 300)
        content = sanitize_untrusted_text(" ".join(parser.parts), self.maximum_characters)
        return current, title, content

