"""Optional bounded localhost REST server managed by Runtime."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import threading
from typing import Any
from urllib.parse import urlsplit

from Isabella.Core.config import ConfigurationError, PROJECT_ROOT
from .auth import RateLimiter, TokenAuthentication
from .models import APIResponse
from .routes import APIRoutes


DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "api.json"


def load_api_config(path: Path | None = None) -> dict[str, Any]:
    target = path or DEFAULT_CONFIG_PATH
    try:
        config = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Invalid API configuration: {target}") from exc
    required = {"enabled", "host", "port", "allow_remote", "authentication_required", "token_file", "cors_allowed_origins", "command_rate_limit", "rate_window_seconds", "max_request_bytes"}
    if not isinstance(config, dict) or required - config.keys():
        raise ConfigurationError("API configuration is missing required fields")
    try:
        address = ipaddress.ip_address(config["host"])
    except ValueError as exc:
        raise ConfigurationError("API host must be a literal IP address") from exc
    if not config["allow_remote"] and not address.is_loopback:
        raise ConfigurationError("Remote API binding requires allow_remote=true")
    if config["allow_remote"] and not config["authentication_required"]:
        raise ConfigurationError("Remote API access requires authentication")
    if not 0 <= int(config["port"]) <= 65535 or not 1 <= int(config["command_rate_limit"]) <= 1000:
        raise ConfigurationError("API port or rate limit is invalid")
    if not 1 <= float(config["rate_window_seconds"]) <= 3600 or not 128 <= int(config["max_request_bytes"]) <= 1_000_000:
        raise ConfigurationError("API request limits are invalid")
    if not isinstance(config["cors_allowed_origins"], list) or "*" in config["cors_allowed_origins"]:
        raise ConfigurationError("Wildcard CORS is forbidden")
    return config


class LocalAPIServer:
    def __init__(self, config: dict[str, Any], *, brain, runtime=None, event_bus=None) -> None:
        self.config = config
        self.enabled = bool(config["enabled"])
        self.host = str(config["host"])
        self.port = int(config["port"])
        token_path = Path(config["token_file"])
        self.authentication = TokenAuthentication(token_path if token_path.is_absolute() else PROJECT_ROOT / token_path, bool(config["authentication_required"]))
        self.rate_limiter = RateLimiter(int(config["command_rate_limit"]), float(config["rate_window_seconds"]))
        self.routes = APIRoutes(brain=brain, runtime=runtime, event_bus=event_bus, authentication=self.authentication, rate_limiter=self.rate_limiter)
        self.status = "DISABLED" if not self.enabled else "OFFLINE"
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @classmethod
    def from_config(cls, *, brain, runtime=None, event_bus=None, path: Path | None = None) -> "LocalAPIServer":
        return cls(load_api_config(path), brain=brain, runtime=runtime, event_bus=event_bus)

    def start(self) -> bool:
        if not self.enabled:
            self.status = "DISABLED"
            return True
        if self._thread and self._thread.is_alive():
            return True
        try:
            self.authentication.initialize()
            handler = self._build_handler()
            self._server = ThreadingHTTPServer((self.host, self.port), handler)
            self._server.daemon_threads = True
            self.port = int(self._server.server_address[1])
            self._thread = threading.Thread(target=self._server.serve_forever, name="IsabellaLocalAPI", daemon=True)
            self._thread.start()
            self.status = "ONLINE"
            return True
        except Exception:
            self.status = "ERROR"
            self.routes.errors += 1
            return False

    def shutdown(self) -> bool:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self._thread:
            self._thread.join(2)
        alive = bool(self._thread and self._thread.is_alive())
        self.status = "ERROR" if alive else ("DISABLED" if not self.enabled else "OFFLINE")
        self._server = None
        self._thread = None
        return not alive

    def health_check(self) -> dict[str, Any]:
        details = self.routes.diagnostics(self.status)
        details.update({"host": self.host, "port": self.port, "allow_remote": bool(self.config["allow_remote"]), "authentication_required": self.authentication.required})
        return details

    def _build_handler(self):
        routes = self.routes
        max_bytes = int(self.config["max_request_bytes"])
        allowed_origins = frozenset(self.config["cors_allowed_origins"])

        class Handler(BaseHTTPRequestHandler):
            server_version = "ISABELLA-LocalAPI/1.0"

            def do_GET(self):
                self._dispatch(None)

            def do_POST(self):
                length = self.headers.get("Content-Length")
                if length is None or not length.isdigit() or int(length) > max_bytes:
                    code, response = routes.reject_request("POST", urlsplit(self.path).path, {key.lower(): value for key, value in self.headers.items()}, 413, "Payload muito grande ou ausente.", "INVALID_CONTENT_LENGTH")
                    self._write(code, response)
                    return
                try:
                    payload = json.loads(self.rfile.read(int(length)).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    code, response = routes.reject_request("POST", urlsplit(self.path).path, {key.lower(): value for key, value in self.headers.items()}, 400, "JSON inválido.", "INVALID_JSON")
                    self._write(code, response)
                    return
                self._dispatch(payload)

            def _dispatch(self, payload):
                path = urlsplit(self.path).path
                headers = {key.lower(): value for key, value in self.headers.items()}
                code, response = routes.dispatch(self.command, path, payload, headers, self.client_address[0])
                self._write(code, response)

            def _write(self, code: int, response: APIResponse):
                body = json.dumps(response.to_dict(), ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("X-Request-ID", response.request_id)
                origin = self.headers.get("Origin")
                if origin in allowed_origins:
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Vary", "Origin")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                return

        return Handler
