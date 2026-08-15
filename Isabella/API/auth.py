"""Local bearer token storage and bounded request rate limiting."""

from __future__ import annotations

from collections import OrderedDict, deque
import hmac
from pathlib import Path
import secrets
import threading
from time import monotonic


class TokenAuthentication:
    def __init__(self, token_path: Path, required: bool = True) -> None:
        self.token_path = token_path
        self.required = required
        self._token: str | None = None

    def initialize(self) -> None:
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        if self.token_path.exists():
            token = self.token_path.read_text(encoding="utf-8").strip()
            if len(token) < 32:
                raise ValueError("Local API token is invalid")
        else:
            token = secrets.token_urlsafe(48)
            self.token_path.write_text(token, encoding="utf-8")
            try:
                self.token_path.chmod(0o600)
            except OSError:
                pass
        self._token = token

    def validate_header(self, authorization: str | None) -> bool:
        if not self.required:
            return True
        if not self._token or not authorization or not authorization.startswith("Bearer "):
            return False
        return hmac.compare_digest(authorization[7:].strip(), self._token)

    @property
    def token_for_local_setup(self) -> str:
        if self._token is None:
            raise RuntimeError("Authentication is not initialized")
        return self._token


class RateLimiter:
    def __init__(self, limit: int, window_seconds: float, max_clients: int = 1000) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.max_clients = max_clients
        self._requests: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.RLock()

    def allow(self, client_id: str) -> bool:
        now = monotonic()
        with self._lock:
            history = self._requests.setdefault(client_id, deque(maxlen=self.limit))
            self._requests.move_to_end(client_id)
            while history and now - history[0] >= self.window_seconds:
                history.popleft()
            if len(history) >= self.limit:
                return False
            history.append(now)
            while len(self._requests) > self.max_clients:
                self._requests.popitem(last=False)
            return True

