"""Versioned declarative Skill Forge candidate models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any

from Isabella.Skills.base import RiskLevel


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class ForgeState(str, Enum):
    DRAFT = "DRAFT"
    VALIDATING = "VALIDATING"
    TESTING = "TESTING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    APPROVED = "APPROVED"
    ENABLED = "ENABLED"
    REJECTED = "REJECTED"
    DISABLED = "DISABLED"


ALLOWED_TRANSITIONS = {
    ForgeState.DRAFT: {ForgeState.VALIDATING, ForgeState.REJECTED},
    ForgeState.VALIDATING: {ForgeState.TESTING, ForgeState.REJECTED},
    ForgeState.TESTING: {ForgeState.WAITING_APPROVAL, ForgeState.REJECTED},
    ForgeState.WAITING_APPROVAL: {ForgeState.APPROVED, ForgeState.REJECTED},
    ForgeState.APPROVED: {ForgeState.ENABLED, ForgeState.REJECTED},
    ForgeState.ENABLED: {ForgeState.DISABLED, ForgeState.REJECTED},
    ForgeState.DISABLED: {ForgeState.REJECTED},
    ForgeState.REJECTED: set(),
}


@dataclass(frozen=True)
class ForgeInput:
    name: str
    type: str
    required: bool = True
    example: Any = None


@dataclass(frozen=True)
class ForgeStep:
    skill_id: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GeneratedTest:
    name: str
    inputs: dict[str, Any]
    expected_steps: tuple[str, ...]


@dataclass(frozen=True)
class SkillSpec:
    id: str
    name: str
    description: str
    inputs: tuple[ForgeInput, ...]
    outputs: dict[str, str]
    risk_level: RiskLevel
    dependencies: tuple[str, ...]
    steps: tuple[ForgeStep, ...]
    permissions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["risk_level"] = self.risk_level.value
        return value


@dataclass
class SkillCandidate:
    spec: SkillSpec
    tests: tuple[GeneratedTest, ...]
    code: str = ""
    state: ForgeState = ForgeState.DRAFT
    version: str = "1.0.0"
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    checksum: str = ""
    origin: str = "manual_structured"
    validation_errors: tuple[str, ...] = ()
    test_results: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.checksum:
            self.checksum = self.calculate_checksum()

    def calculate_checksum(self) -> str:
        payload = {
            "spec": self.spec.to_dict(), "tests": [asdict(test) for test in self.tests],
            "code": self.code, "version": self.version, "origin": self.origin,
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @property
    def approval_token(self) -> str:
        return f"APPROVE-{self.checksum[:12].upper()}"

    def transition(self, state: ForgeState) -> None:
        if state not in ALLOWED_TRANSITIONS[self.state]:
            raise ValueError(f"Invalid Skill Forge transition: {self.state.value} -> {state.value}")
        self.state = state
        self.updated_at = utc_now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec": self.spec.to_dict(), "tests": [asdict(test) for test in self.tests],
            "code": self.code, "state": self.state.value, "version": self.version,
            "created_at": self.created_at, "updated_at": self.updated_at,
            "checksum": self.checksum, "origin": self.origin,
            "validation_errors": list(self.validation_errors), "test_results": list(self.test_results),
        }


@dataclass(frozen=True)
class ValidationReport:
    valid: bool
    errors: tuple[str, ...] = ()
    notices: tuple[str, ...] = ()


@dataclass(frozen=True)
class SandboxReport:
    passed: bool
    results: tuple[str, ...]


@dataclass(frozen=True)
class ApprovalPreview:
    skill_id: str
    description: str
    permissions: tuple[str, ...]
    modified_files: tuple[str, ...]
    risk_level: str
    dependencies: tuple[str, ...]
    steps: tuple[str, ...]
    approval_token: str
