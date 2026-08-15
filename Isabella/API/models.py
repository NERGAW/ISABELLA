"""Structured local API response models."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class APIResponse:
    success: bool
    request_id: str
    message: str
    status: str
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success, "request_id": self.request_id,
            "message": self.message, "status": self.status,
            "data": self.data, "error": self.error,
        }

