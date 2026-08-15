"""Allowlisted REST route dispatcher; no filesystem or arbitrary execution."""

from __future__ import annotations

from typing import Any
import uuid

from Isabella.Events import EventType
from Isabella.Protocol.version import PROTOCOL_NAME, PROTOCOL_VERSION
from .models import APIResponse


class APIRoutes:
    def __init__(self, *, brain, runtime=None, event_bus=None, authentication, rate_limiter) -> None:
        self.brain = brain
        self.runtime = runtime
        self.event_bus = event_bus
        self.authentication = authentication
        self.rate_limiter = rate_limiter
        self.requests = 0
        self.errors = 0
        self.auth_failures = 0
        self.rate_limited = 0

    def dispatch(self, method: str, path: str, payload: Any, headers: dict[str, str], client: str) -> tuple[int, APIResponse]:
        request_id = headers.get("x-request-id") or uuid.uuid4().hex
        self.requests += 1
        self._emit(EventType.API_REQUEST_RECEIVED, request_id, {"method": method, "path": path})
        if path == "/health" and method == "GET":
            return self._complete(200, APIResponse(True, request_id, "API local operacional.", "ONLINE", {"local": True, "protocol": {"name": PROTOCOL_NAME, "version": PROTOCOL_VERSION, "transport_enabled": False}}))
        if not self.authentication.validate_header(headers.get("authorization")):
            self.auth_failures += 1
            self._emit(EventType.API_AUTH_FAILED, request_id, {"path": path})
            return 401, APIResponse(False, request_id, "Autenticação necessária.", "unauthorized", error="AUTH_FAILED")
        try:
            if path == "/status" and method == "GET":
                data = self.runtime.report() if self.runtime else {"runtime": "UNKNOWN"}
                data["protocol"] = {"name": PROTOCOL_NAME, "version": PROTOCOL_VERSION, "transport_enabled": False}
                return self._complete(200, APIResponse(True, request_id, "Estado consultado.", "completed", data))
            if path == "/skills" and method == "GET":
                skills = [
                    {"id": item.id, "name": item.name, "description": item.description, "category": item.category, "risk_level": item.risk_level.value, "enabled": item.enabled}
                    for item in self.brain.registry.list()
                ]
                return self._complete(200, APIResponse(True, request_id, "Skills autorizadas.", "completed", {"skills": skills}))
            if path == "/diagnostics" and method == "GET":
                report = self.brain.diagnostics.check(detailed=True, expensive=False)
                return self._complete(200, APIResponse(True, request_id, report.summary, "completed", {"report": report.to_dict()}))
            if path == "/command" and method == "POST":
                if not self.rate_limiter.allow(client):
                    self.rate_limited += 1
                    self._emit(EventType.API_RATE_LIMITED, request_id, {"client": client})
                    return 429, APIResponse(False, request_id, "Limite de requisições excedido.", "rate_limited", error="RATE_LIMITED")
                if not isinstance(payload, dict) or set(payload) != {"text"} or not isinstance(payload.get("text"), str) or not payload["text"].strip() or len(payload["text"]) > 4000:
                    return self._error(400, request_id, "Payload inválido.", "INVALID_PAYLOAD")
                response = self.brain.process(payload["text"].strip(), request_id=request_id, input_source="api")
                results = [item.to_dict() for item in response.skill_results]
                status = results[-1]["status"] if results else "completed"
                success = all(item["success"] for item in results) if results else True
                error = next((item.get("error_code") for item in results if not item["success"]), None)
                data = {"response_type": response.response_type.value, "skill_results": results}
                return self._complete(200, APIResponse(success, request_id, response.message, status, data, error))
            return self._error(404, request_id, "Endpoint não encontrado.", "NOT_FOUND")
        except Exception as exc:
            return self._error(500, request_id, "A requisição falhou.", type(exc).__name__)

    def reject_request(self, method: str, path: str, headers: dict[str, str], code: int, message: str, error: str) -> tuple[int, APIResponse]:
        request_id = headers.get("x-request-id") or uuid.uuid4().hex
        self.requests += 1
        self._emit(EventType.API_REQUEST_RECEIVED, request_id, {"method": method, "path": path})
        return self._error(code, request_id, message, error)

    def diagnostics(self, status: str) -> dict[str, Any]:
        return {"status": status, "requests": self.requests, "errors": self.errors, "auth_failures": self.auth_failures, "rate_limited": self.rate_limited}

    def _complete(self, code: int, response: APIResponse) -> tuple[int, APIResponse]:
        self._emit(EventType.API_REQUEST_COMPLETED, response.request_id, {"status": response.status, "success": response.success})
        return code, response

    def _error(self, code: int, request_id: str, message: str, error: str) -> tuple[int, APIResponse]:
        self.errors += 1
        return self._complete(code, APIResponse(False, request_id, message, "error", error=error))

    def _emit(self, event_type, request_id: str, payload: dict[str, Any]) -> None:
        if self.event_bus:
            self.event_bus.emit(event_type, "api", payload, correlation_id=request_id)
