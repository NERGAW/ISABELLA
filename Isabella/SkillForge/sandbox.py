"""Effect-free declarative dry-run sandbox for composite candidates."""

from __future__ import annotations

from .models import SandboxReport, SkillCandidate


class SkillSandbox:
    def __init__(self, registry) -> None:
        self.registry = registry

    def test(self, candidate: SkillCandidate) -> SandboxReport:
        if candidate.code:
            return SandboxReport(False, ("generated code execution is disabled",))
        if not candidate.tests:
            return SandboxReport(False, ("no generated tests",))
        results: list[str] = []
        passed = True
        for test in candidate.tests:
            observed: list[str] = []
            for step in candidate.spec.steps:
                arguments = self._resolve(step.arguments, test.inputs)
                validation = self.registry.validate_arguments(step.skill_id, arguments)
                if validation:
                    passed = False
                    results.append(f"{test.name}:FAIL:{step.skill_id}:{validation.error_code}")
                    break
                observed.append(step.skill_id)
            else:
                if tuple(observed) != test.expected_steps:
                    passed = False
                    results.append(f"{test.name}:FAIL:unexpected_steps")
                else:
                    results.append(f"{test.name}:PASS")
        return SandboxReport(passed, tuple(results))

    @classmethod
    def _resolve(cls, value, inputs: dict):
        if isinstance(value, dict):
            if set(value) == {"$input"}:
                return inputs.get(value["$input"])
            return {key: cls._resolve(item, inputs) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._resolve(item, inputs) for item in value]
        return value

