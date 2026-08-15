import pytest

from Isabella.Events import EventType
from Isabella.Security import SecurityPolicyEngine
from Isabella.SkillForge import ForgeState, SkillForgeManager
from Isabella.SkillForge.models import SkillCandidate
from Isabella.SkillForge.validator import SkillValidator
from Isabella.Skills.base import ParameterSpec, RiskLevel, SkillDefinition, SkillResult
from Isabella.Skills.registry import SkillRegistry


CONFIG = {
    "enabled": True,
    "storage_directory": "unused",
    "max_steps": 8,
    "allowed_dependencies": [],
    "allow_generated_code": False,
    "require_explicit_approval": True,
}


class Events:
    def __init__(self):
        self.types = []

    def emit(self, event_type, source, data):
        self.types.append(event_type)


def registry(executions=None):
    executions = executions if executions is not None else []
    result = SkillRegistry(policy_engine=SecurityPolicyEngine({
        "confirmation_timeout_seconds": 60,
        "risk_policies": {"SAFE": "ALLOW", "CAUTION": "ALLOW", "CRITICAL": "CONFIRM"},
        "critical_confirmation_required": True, "logging_level": "INFO",
    }))

    def add(skill_id, parameters, risk=RiskLevel.SAFE):
        def execute(arguments):
            executions.append((skill_id, arguments))
            return SkillResult(True, skill_id, "ok")
        result.register(SkillDefinition(skill_id, skill_id, "test", "test", parameters, risk, execute))

    add("applications.open", {"name": ParameterSpec(str)})
    add("browser.open_url", {"url": ParameterSpec(str)})
    add("system.shutdown", {}, RiskLevel.CRITICAL)
    return result


def specification(skill_id="custom.prepare_work"):
    return {
        "skill_id": skill_id,
        "name": "Preparar trabalho",
        "description": "Abre VS Code, Chrome e GitHub.",
        "steps": [
            {"skill_id": "applications.open", "arguments": {"name": "vscode"}},
            {"skill_id": "applications.open", "arguments": {"name": "chrome"}},
            {"skill_id": "browser.open_url", "arguments": {"url": "https://github.com"}},
        ],
        "permissions": ["launch_application", "open_public_url"],
    }


def test_full_lifecycle_requires_separate_approval_and_enable(tmp_path):
    executions, events = [], Events()
    skills = registry(executions)
    forge = SkillForgeManager(CONFIG, registry=skills, event_bus=events, storage_directory=tmp_path)

    candidate = forge.create_draft(**specification())
    assert candidate.state is ForgeState.DRAFT
    assert not skills.exists(candidate.spec.id)
    assert forge.validate_candidate(candidate.spec.id).state is ForgeState.WAITING_APPROVAL
    assert executions == []  # Sandbox validates schemas but never invokes executors.

    preview = forge.preview(candidate.spec.id)
    assert preview.steps == ("applications.open", "applications.open", "browser.open_url")
    with pytest.raises(PermissionError):
        forge.approve(candidate.spec.id, "wrong")
    assert forge.approve(candidate.spec.id, preview.approval_token).state is ForgeState.APPROVED
    assert not skills.exists(candidate.spec.id)
    assert forge.enable(candidate.spec.id).state is ForgeState.ENABLED
    assert skills.execute(candidate.spec.id, {}).success
    assert len(executions) == 3
    assert EventType.SKILLFORGE_DRAFT_CREATED in events.types
    assert EventType.SKILLFORGE_APPROVED in events.types
    assert EventType.SKILLFORGE_ENABLED in events.types


def test_duplicate_semantics_and_single_step_wrapper_are_rejected(tmp_path):
    forge = SkillForgeManager(CONFIG, registry=registry(), storage_directory=tmp_path)
    forge.create_draft(**specification("custom.first"))
    with pytest.raises(ValueError):
        forge.create_draft(**specification("custom.same_actions"))
    one = specification("custom.wrapper")
    one["steps"] = one["steps"][:1]
    candidate = forge.create_draft(**one)
    assert forge.validate_candidate(candidate.spec.id).state is ForgeState.REJECTED
    assert "DUPLICATE_WRAPPER:applications.open" in candidate.validation_errors


@pytest.mark.parametrize("code,marker", [
    ("result = eval('2 + 2')", "FORBIDDEN_CALL:eval"),
    ("import subprocess\nsubprocess.run(['whoami'])", "FORBIDDEN_IMPORT:subprocess"),
    ("import os\nvalue = os.getenv('TOKEN')", "SECRET_ENVIRONMENT_ACCESS"),
])
def test_static_validator_rejects_dangerous_generated_code(tmp_path, code, marker):
    skills = registry()
    base = SkillForgeManager(CONFIG, registry=skills, storage_directory=tmp_path).generator.create_composite(**specification())
    candidate = SkillCandidate(base.spec, base.tests, code=code)
    assert marker in SkillValidator(skills).validate(candidate).errors


def test_dependencies_need_exact_separate_approval(tmp_path):
    forge = SkillForgeManager(CONFIG, registry=registry(), storage_directory=tmp_path)
    spec = specification()
    spec["dependencies"] = ["optional-package"]
    candidate = forge.create_draft(**spec)
    forge.validate_candidate(candidate.spec.id)
    preview = forge.preview(candidate.spec.id)
    with pytest.raises(PermissionError):
        forge.approve(candidate.spec.id, preview.approval_token)
    assert forge.approve(
        candidate.spec.id, preview.approval_token,
        approved_dependencies=("optional-package",),
    ).state is ForgeState.APPROVED


def test_missing_tests_unknown_step_and_invalid_transition_are_rejected(tmp_path):
    skills = registry()
    forge = SkillForgeManager(CONFIG, registry=skills, storage_directory=tmp_path)
    candidate = forge.generator.create_composite(**specification())
    candidate.tests = ()
    assert "TESTS_REQUIRED" in forge.validator.validate(candidate).errors
    with pytest.raises(ValueError):
        candidate.transition(ForgeState.ENABLED)
    bad = specification("custom.bad")
    bad["steps"][1]["skill_id"] = "unknown.skill"
    rejected = forge.create_draft(**bad)
    assert forge.validate_candidate(rejected.spec.id).state is ForgeState.REJECTED


def test_checksum_blocks_tampering_and_commit_export_is_explicit(tmp_path):
    forge = SkillForgeManager(CONFIG, registry=registry(), storage_directory=tmp_path)
    candidate = forge.create_draft(**specification())
    forge.validate_candidate(candidate.spec.id)
    with pytest.raises(PermissionError):
        forge.export_for_commit(candidate.spec.id)
    preview = forge.preview(candidate.spec.id)
    forge.approve(candidate.spec.id, preview.approval_token)
    assert forge.export_for_commit(candidate.spec.id).exists()
    candidate.spec.steps[0].arguments["name"] = "tampered"
    with pytest.raises(PermissionError):
        forge.enable(candidate.spec.id)


def test_risk_is_maximum_of_composed_skills(tmp_path):
    forge = SkillForgeManager(CONFIG, registry=registry(), storage_directory=tmp_path)
    spec = specification()
    spec["steps"].append({"skill_id": "system.shutdown", "arguments": {}})
    candidate = forge.create_draft(**spec)
    assert candidate.spec.risk_level is RiskLevel.CRITICAL
