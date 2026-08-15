import json

import pytest

from Isabella.Intelligence.brain import Brain
from Isabella.Intelligence.models import Plan, PlanStep
from Isabella.Skills.applications import ApplicationResolver
from Isabella.Skills.base import ParameterSpec, RiskLevel, SkillDefinition, SkillResult
from Isabella.Skills.browser import create_browser_skills, normalize_url
from Isabella.Skills.registry import SkillRegistry
from Isabella.Skills.system import create_system_skills


def definition(skill_id="test.echo", risk=RiskLevel.SAFE, executor=None):
    return SkillDefinition(
        skill_id,
        "Echo",
        "Test skill",
        "test",
        {"value": ParameterSpec(str)},
        risk,
        executor or (lambda args: SkillResult(True, skill_id, args["value"])),
    )


def test_registry_registration_and_category():
    registry = SkillRegistry()
    skill = definition()
    registry.register(skill)

    assert registry.exists("test.echo")
    assert registry.get("test.echo") == skill
    assert registry.list() == [skill]
    assert registry.list_by_category("test") == [skill]


def test_registry_rejects_duplicate():
    registry = SkillRegistry()
    registry.register(definition())

    with pytest.raises(ValueError, match="already registered"):
        registry.register(definition())


def test_registry_rejects_unknown_skill():
    result = SkillRegistry().execute("system.launch_missile", {})

    assert result.error_code == "UNKNOWN_SKILL"
    assert result.status == "rejected"


@pytest.mark.parametrize(
    ("arguments", "error_code"),
    [({}, "MISSING_ARGUMENTS"), ({"value": 10}, "INVALID_ARGUMENT_TYPE"), ({"value": "ok", "extra": 1}, "EXTRA_ARGUMENTS")],
)
def test_argument_validation(arguments, error_code):
    registry = SkillRegistry()
    registry.register(definition())

    assert registry.execute("test.echo", arguments).error_code == error_code


def test_application_aliases(tmp_path):
    config = {
        "chrome": {"aliases": ["chrome", "navegador"], "paths": [], "executables": ["chrome.exe"]},
        "vscode": {"aliases": ["editor de código"], "paths": [], "executables": ["Code.exe"]},
    }
    path = tmp_path / "applications.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    resolver = ApplicationResolver(path)

    assert resolver.normalize("navegador") == "chrome"
    assert resolver.normalize("editor de código") == "vscode"
    assert resolver.normalize("missing") is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [("youtube", "https://youtube.com"), ("https://github.com/openai", "https://github.com/openai"), ("file:///etc/passwd", None), ("javascript:alert(1)", None)],
)
def test_browser_url_validation(value, expected):
    assert normalize_url(value) == expected


def test_browser_requires_target():
    registry = SkillRegistry()
    registry.register(create_browser_skills(opener=lambda url: True)[0])

    result = registry.execute("browser.open_url", {})

    assert result.error_code == "MISSING_TARGET"


@pytest.mark.parametrize("value", [-1, 101])
def test_volume_boundaries(value):
    registry = SkillRegistry()
    volume = next(skill for skill in create_system_skills() if skill.id == "system.set_volume")
    registry.register(volume)

    result = registry.execute("system.set_volume", {"value": value})

    assert result.error_code == "VOLUME_OUT_OF_RANGE"


def test_critical_requires_confirmation_without_execution():
    calls = []
    registry = SkillRegistry()
    registry.register(definition("system.shutdown", RiskLevel.CRITICAL, lambda args: calls.append(args)))

    result = registry.execute("system.shutdown", {"value": "now"})

    assert result.status == "confirmation_required"
    assert calls == []


def test_planner_success_is_executed_in_dependency_order():
    calls = []
    registry = SkillRegistry()
    for skill_id in ("test.first", "test.second"):
        registry.register(definition(skill_id, executor=lambda args, current=skill_id: (calls.append(current) or SkillResult(True, current, "ok"))))
    brain = Brain(object(), registry=registry)
    plan = Plan([
        PlanStep(1, "test.first", {"value": "one"}),
        PlanStep(2, "test.second", {"value": "two"}, [1]),
    ])

    results = brain._execute_plan(plan)

    assert calls == ["test.first", "test.second"]
    assert all(result.success for result in results)


def test_planner_stops_after_dependency_failure():
    calls = []
    registry = SkillRegistry()
    registry.register(definition("test.first", executor=lambda args: SkillResult(False, "test.first", "failed", error_code="FAIL", status="failed")))
    registry.register(definition("test.second", executor=lambda args: (calls.append("second") or SkillResult(True, "test.second", "ok"))))
    brain = Brain(object(), registry=registry)
    plan = Plan([
        PlanStep(1, "test.first", {"value": "one"}),
        PlanStep(2, "test.second", {"value": "two"}, [1]),
    ])

    results = brain._execute_plan(plan)

    assert len(results) == 1
    assert results[0].error_code == "FAIL"
    assert calls == []
