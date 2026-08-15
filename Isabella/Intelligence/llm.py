"""Small Ollama HTTP provider without business logic or tool execution."""

import json
import logging
from collections import deque
from pathlib import Path
import threading
from time import perf_counter
from typing import Any

import requests

from Isabella.Core.config import ConfigurationError, PROJECT_ROOT


LOGGER = logging.getLogger("LLM")
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "intelligence.json"
REQUIRED_CONFIG = {
    "provider",
    "model",
    "base_url",
    "temperature",
    "timeout_seconds",
    "max_retries",
    "max_plan_steps",
}

SYSTEM_PROMPT = """Você é a I.S.A.B.E.L.L.A. (ISABELLA), Intelligent System for Adaptive
Behavior, Environment, Learning, Logic and Assistance. Seu idioma principal é pt-BR.
Responda com clareza e use respostas curtas para comandos. Nunca afirme que executou uma
ação que não foi executada. Quando uma estrutura for solicitada, retorne somente uma
estrutura válida. Nunca gere comandos shell para execução automática. Não imite personagens
ou outros assistentes."""


class IntelligenceConfigError(ConfigurationError):
    pass


class ProviderUnavailableError(RuntimeError):
    pass


class InvalidStructuredResponseError(RuntimeError):
    pass


def load_intelligence_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or DEFAULT_CONFIG_PATH
    if not config_path.is_file():
        raise IntelligenceConfigError(f"Intelligence configuration not found: {config_path}")
    try:
        with config_path.open(encoding="utf-8") as config_file:
            config = json.load(config_file)
    except json.JSONDecodeError as exc:
        raise IntelligenceConfigError(f"Invalid intelligence JSON: {exc.msg}") from exc
    missing = sorted(REQUIRED_CONFIG - config.keys()) if isinstance(config, dict) else []
    if not isinstance(config, dict) or missing:
        raise IntelligenceConfigError(
            "Invalid intelligence configuration"
            + (f"; missing: {', '.join(missing)}" if missing else "")
        )
    if config["provider"] != "ollama":
        raise IntelligenceConfigError("Only the ollama provider is supported in this phase")
    if config["max_retries"] < 0 or config["max_plan_steps"] < 1:
        raise IntelligenceConfigError("Retry and plan limits are invalid")
    return config


class OllamaProvider:
    def __init__(self, config: dict[str, Any]) -> None:
        self.model = str(config["model"])
        self.base_url = str(config["base_url"]).rstrip("/")
        self.temperature = float(config["temperature"])
        self.timeout = float(config["timeout_seconds"])
        self.max_retries = int(config["max_retries"])
        self.latencies_ms: deque[float] = deque(maxlen=200)
        self._session = requests.Session()
        self._session_lock = threading.Lock()

    @classmethod
    def from_config(cls, path: Path | None = None) -> "OllamaProvider":
        return cls(load_intelligence_config(path))

    def _request(self, method: str, endpoint: str, **kwargs: Any) -> requests.Response:
        last_error: requests.RequestException | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with self._session_lock:
                    response = self._session.request(
                        method, f"{self.base_url}{endpoint}", timeout=self.timeout, **kwargs
                    )
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                last_error = exc
                LOGGER.warning("request_failed endpoint=%s attempt=%d", endpoint, attempt + 1)
        raise ProviderUnavailableError(
            f"Intelligence provider unavailable at {self.base_url}"
        ) from last_error

    def health_check(self) -> bool:
        try:
            self._request("GET", "/api/version")
            return True
        except ProviderUnavailableError:
            return False

    def list_models(self) -> list[str]:
        data = self._request("GET", "/api/tags").json()
        models = data.get("models", []) if isinstance(data, dict) else []
        return [item["name"] for item in models if isinstance(item, dict) and "name" in item]

    def chat(self, message: str, system_prompt: str = SYSTEM_PROMPT) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message},
            ],
            "stream": False,
            "think": False,
            "options": {"temperature": self.temperature},
        }
        started = perf_counter()
        try:
            data = self._request("POST", "/api/chat", json=payload).json()
            content = data.get("message", {}).get("content") if isinstance(data, dict) else None
            if not isinstance(content, str) or not content.strip():
                raise ProviderUnavailableError("Ollama returned an empty chat response")
            return content.strip()
        finally:
            latency = (perf_counter() - started) * 1000
            self.latencies_ms.append(latency)
            LOGGER.info("model=%s latency_ms=%.2f", self.model, latency)

    def structured_chat(self, message: str, schema: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": message},
            ],
            "format": schema,
            "stream": False,
            "think": False,
            "options": {"temperature": self.temperature},
        }
        started = perf_counter()
        try:
            response = self._request("POST", "/api/chat", json=payload).json()
            content = response.get("message", {}).get("content", "")
            try:
                value = json.loads(content)
            except (TypeError, json.JSONDecodeError) as exc:
                raise InvalidStructuredResponseError("Ollama returned invalid structured JSON") from exc
            if not isinstance(value, dict):
                raise InvalidStructuredResponseError("Structured response must be an object")
            return value
        finally:
            latency = (perf_counter() - started) * 1000
            self.latencies_ms.append(latency)
            LOGGER.info("model=%s latency_ms=%.2f structured=true", self.model, latency)

    @property
    def average_latency_ms(self) -> float:
        return sum(self.latencies_ms) / len(self.latencies_ms) if self.latencies_ms else 0.0

    def close(self) -> None:
        with self._session_lock:
            self._session.close()
