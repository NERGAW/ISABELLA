"""Controlled Skill Forge lifecycle, persistence, approval and registration."""

from __future__ import annotations

from dataclasses import asdict
import json
import logging
from pathlib import Path
import threading
from typing import Any

from Isabella.Core.config import ConfigurationError, PROJECT_ROOT
from Isabella.Events import EventType
from Isabella.Skills.base import ParameterSpec, RiskLevel, SkillDefinition, SkillResult
from .generator import SkillGenerator
from .models import (
    ApprovalPreview, ForgeInput, ForgeState, ForgeStep, GeneratedTest,
    SkillCandidate, SkillSpec,
)
from .sandbox import SkillSandbox
from .validator import SkillValidator


LOGGER = logging.getLogger("SKILL_FORGE")
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "skillforge.json"
TYPE_MAP = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}


def load_skillforge_config(path: Path | None = None) -> dict[str, Any]:
    target = path or DEFAULT_CONFIG_PATH
    try:
        config = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Invalid Skill Forge configuration: {target}") from exc
    required = {"enabled", "storage_directory", "max_steps", "allowed_dependencies", "allow_generated_code", "require_explicit_approval"}
    if not isinstance(config, dict) or required - config.keys():
        raise ConfigurationError("Skill Forge configuration is missing required fields")
    if not 1 <= int(config["max_steps"]) <= 20 or not isinstance(config["allowed_dependencies"], list):
        raise ConfigurationError("Skill Forge limits are invalid")
    if config["allow_generated_code"] is not False or config["require_explicit_approval"] is not True:
        raise ConfigurationError("Skill Forge safety controls cannot be weakened")
    return config


class SkillForgeManager:
    def __init__(self, config: dict[str, Any], *, registry, event_bus=None, storage_directory: Path | None = None) -> None:
        self.config = config
        self.enabled = bool(config["enabled"])
        self.registry = registry
        self.event_bus = event_bus
        configured = Path(config["storage_directory"])
        self.storage_directory = storage_directory or (configured if configured.is_absolute() else PROJECT_ROOT / configured)
        self.storage_directory.mkdir(parents=True, exist_ok=True)
        self.generator = SkillGenerator(registry)
        self.validator = SkillValidator(registry, set(config["allowed_dependencies"]), int(config["max_steps"]))
        self.sandbox = SkillSandbox(registry)
        self._candidates: dict[str, SkillCandidate] = {}
        self._lock = threading.RLock()
        self._load()

    @classmethod
    def from_config(cls, *, registry, path: Path | None = None, **components) -> "SkillForgeManager":
        return cls(load_skillforge_config(path), registry=registry, **components)

    def start(self) -> bool:
        if not self.enabled:
            return True
        for candidate in self.list_candidates():
            if candidate.state is ForgeState.ENABLED and candidate.calculate_checksum() == candidate.checksum:
                self._register(candidate)
        return True

    def create_draft(self, **specification) -> SkillCandidate:
        if not self.enabled:
            raise RuntimeError("Skill Forge is disabled")
        candidate = self.generator.create_composite(**specification)
        with self._lock:
            if candidate.spec.id in self._candidates or self.registry.exists(candidate.spec.id):
                raise ValueError("Equivalent or duplicate Skill already exists")
            signature = self._signature(candidate)
            if any(self._signature(item) == signature for item in self._candidates.values()):
                raise ValueError("Equivalent or duplicate Skill already exists")
            self._candidates[candidate.spec.id] = candidate
        self._persist(candidate)
        self._emit(EventType.SKILLFORGE_DRAFT_CREATED, candidate)
        return candidate

    def validate_candidate(self, skill_id: str) -> SkillCandidate:
        candidate = self._require(skill_id)
        if candidate.state is not ForgeState.DRAFT:
            raise ValueError("Only DRAFT candidates can be validated")
        candidate.transition(ForgeState.VALIDATING)
        report = self.validator.validate(candidate)
        candidate.validation_errors = report.errors
        if not report.valid:
            candidate.transition(ForgeState.REJECTED)
            self._persist(candidate)
            self._emit(EventType.SKILLFORGE_VALIDATION_FAILED, candidate, {"errors": list(report.errors)})
            return candidate
        candidate.transition(ForgeState.TESTING)
        sandbox = self.sandbox.test(candidate)
        candidate.test_results = sandbox.results
        if not sandbox.passed:
            candidate.validation_errors = ("SANDBOX_TEST_FAILED",)
            candidate.transition(ForgeState.REJECTED)
            self._persist(candidate)
            self._emit(EventType.SKILLFORGE_VALIDATION_FAILED, candidate, {"errors": list(candidate.validation_errors)})
            return candidate
        candidate.transition(ForgeState.WAITING_APPROVAL)
        self._persist(candidate)
        self._emit(EventType.SKILLFORGE_WAITING_APPROVAL, candidate)
        return candidate

    def preview(self, skill_id: str) -> ApprovalPreview:
        candidate = self._require(skill_id)
        if candidate.state is not ForgeState.WAITING_APPROVAL:
            raise ValueError("Candidate is not waiting for approval")
        return ApprovalPreview(
            candidate.spec.id, candidate.spec.description, candidate.spec.permissions,
            (str(self._path(candidate)),), candidate.spec.risk_level.value,
            candidate.spec.dependencies, tuple(step.skill_id for step in candidate.spec.steps),
            candidate.approval_token,
        )

    def approve(self, skill_id: str, approval_token: str, *, approved_dependencies: tuple[str, ...] = ()) -> SkillCandidate:
        candidate = self._require(skill_id)
        if candidate.state is not ForgeState.WAITING_APPROVAL:
            raise ValueError("Candidate is not waiting for approval")
        if approval_token != candidate.approval_token:
            raise PermissionError("Explicit approval token is invalid")
        if set(approved_dependencies) != set(candidate.spec.dependencies):
            raise PermissionError("Dependencies require separate explicit approval")
        candidate.transition(ForgeState.APPROVED)
        self._persist(candidate)
        self._emit(EventType.SKILLFORGE_APPROVED, candidate)
        return candidate

    def enable(self, skill_id: str) -> SkillCandidate:
        candidate = self._require(skill_id)
        if candidate.state is not ForgeState.APPROVED:
            raise ValueError("Only APPROVED candidates can be enabled")
        if candidate.calculate_checksum() != candidate.checksum:
            raise PermissionError("Candidate checksum changed after approval")
        self._register(candidate)
        candidate.transition(ForgeState.ENABLED)
        self._persist(candidate)
        self._emit(EventType.SKILLFORGE_ENABLED, candidate)
        return candidate

    def reject(self, skill_id: str) -> SkillCandidate:
        candidate = self._require(skill_id)
        if candidate.state is ForgeState.ENABLED:
            self.registry.unregister(skill_id)
        candidate.transition(ForgeState.REJECTED)
        self._persist(candidate)
        self._emit(EventType.SKILLFORGE_REJECTED, candidate)
        return candidate

    def disable(self, skill_id: str) -> SkillCandidate:
        candidate = self._require(skill_id)
        if candidate.state is not ForgeState.ENABLED:
            raise ValueError("Only ENABLED candidates can be disabled")
        self.registry.unregister(skill_id)
        candidate.transition(ForgeState.DISABLED)
        self._persist(candidate)
        return candidate

    def export_for_commit(self, skill_id: str) -> Path:
        candidate = self._require(skill_id)
        if candidate.state not in {ForgeState.APPROVED, ForgeState.ENABLED, ForgeState.DISABLED}:
            raise PermissionError("Only approved candidates may be explicitly committed")
        return self._path(candidate)

    def list_candidates(self) -> list[SkillCandidate]:
        with self._lock:
            return list(self._candidates.values())

    def diagnostics(self) -> dict[str, int | bool]:
        candidates = self.list_candidates()
        return {
            "enabled": self.enabled, "generated_skills": len(candidates),
            "enabled_skills": sum(item.state is ForgeState.ENABLED for item in candidates),
            "disabled_skills": sum(item.state is ForgeState.DISABLED for item in candidates),
            "failed_validation": sum(
                item.state is ForgeState.REJECTED and bool(item.validation_errors) for item in candidates
            ),
        }

    def shutdown(self) -> bool:
        for candidate in self.list_candidates():
            if candidate.state is ForgeState.ENABLED:
                self.registry.unregister(candidate.spec.id)
        return True

    def _register(self, candidate: SkillCandidate) -> None:
        if self.registry.exists(candidate.spec.id):
            return
        parameters = {
            item.name: ParameterSpec(TYPE_MAP.get(item.type, object), item.required) for item in candidate.spec.inputs
        }

        def execute(arguments: dict[str, Any]) -> SkillResult:
            results = []
            for step in candidate.spec.steps:
                resolved = SkillSandbox._resolve(step.arguments, arguments)
                result = self.registry.execute(step.skill_id, resolved, source_request_id=f"skillforge:{candidate.spec.id}")
                results.append(result.to_dict())
                if not result.success:
                    return SkillResult(False, candidate.spec.id, result.message, {"steps": results}, result.error_code, result.status)
            return SkillResult(True, candidate.spec.id, f"{candidate.spec.name} concluída.", {"steps": results})

        self.registry.register(SkillDefinition(
            candidate.spec.id, candidate.spec.name, candidate.spec.description, "skillforge",
            parameters, candidate.spec.risk_level, execute,
        ))

    def _persist(self, candidate: SkillCandidate) -> None:
        self.storage_directory.mkdir(parents=True, exist_ok=True)
        self._path(candidate).write_text(json.dumps(candidate.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def _load(self) -> None:
        for path in self.storage_directory.glob("*.json"):
            try:
                candidate = self._deserialize(json.loads(path.read_text(encoding="utf-8")))
                if candidate.calculate_checksum() != candidate.checksum:
                    candidate.validation_errors = ("CHECKSUM_MISMATCH",)
                    candidate.transition(ForgeState.REJECTED)
                self._candidates[candidate.spec.id] = candidate
            except Exception:
                LOGGER.warning("candidate_load_failed path=%s", path.name)

    @staticmethod
    def _deserialize(data: dict[str, Any]) -> SkillCandidate:
        spec_data = data["spec"]
        spec = SkillSpec(
            spec_data["id"], spec_data["name"], spec_data["description"],
            tuple(ForgeInput(**item) for item in spec_data.get("inputs", [])),
            dict(spec_data.get("outputs", {})), RiskLevel(spec_data["risk_level"]),
            tuple(spec_data.get("dependencies", [])),
            tuple(ForgeStep(item["skill_id"], dict(item.get("arguments", {}))) for item in spec_data["steps"]),
            tuple(spec_data.get("permissions", [])),
        )
        tests = tuple(
            GeneratedTest(item["name"], dict(item.get("inputs", {})), tuple(item.get("expected_steps", [])))
            for item in data.get("tests", [])
        )
        return SkillCandidate(
            spec, tests, data.get("code", ""), ForgeState(data["state"]), data.get("version", "1.0.0"),
            data["created_at"], data["updated_at"], data["checksum"], data.get("origin", "manual_structured"),
            tuple(data.get("validation_errors", [])), tuple(data.get("test_results", [])),
        )

    def _path(self, candidate: SkillCandidate) -> Path:
        return self.storage_directory / f"{candidate.spec.id.replace('.', '__')}.json"

    @staticmethod
    def _signature(candidate: SkillCandidate) -> str:
        return json.dumps([asdict(step) for step in candidate.spec.steps], sort_keys=True, ensure_ascii=False)

    def _require(self, skill_id: str) -> SkillCandidate:
        with self._lock:
            candidate = self._candidates.get(skill_id)
        if candidate is None:
            raise KeyError(f"Unknown Skill Forge candidate: {skill_id}")
        return candidate

    def _emit(self, event_type, candidate: SkillCandidate, extra: dict[str, Any] | None = None) -> None:
        if self.event_bus:
            payload = {"skill_id": candidate.spec.id, "state": candidate.state.value}
            payload.update(extra or {})
            self.event_bus.emit(event_type, "skillforge", payload)
