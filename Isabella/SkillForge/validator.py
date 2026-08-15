"""Static denial-first validation for generated candidate artifacts."""

from __future__ import annotations

import ast
import re

from .models import SkillCandidate, ValidationReport


FORBIDDEN_IMPORT_PREFIXES = (
    "subprocess", "socket", "requests", "urllib", "httpx", "pip",
    "Isabella.Security", "Isabella.Runtime",
)
FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "os.system", "os.popen"}
SECRET_PATTERNS = re.compile(r"(?:token|secret|password|credential|api[_ -]?key|\.env)", re.IGNORECASE)


class SkillValidator:
    def __init__(self, registry, allowed_dependencies: set[str] | None = None, max_steps: int = 8) -> None:
        self.registry = registry
        self.allowed_dependencies = allowed_dependencies or set()
        self.max_steps = max_steps

    def validate(self, candidate: SkillCandidate) -> ValidationReport:
        errors: list[str] = []
        notices: list[str] = []
        spec = candidate.spec
        if not spec.id or "." not in spec.id:
            errors.append("INVALID_SKILL_ID")
        if self.registry.exists(spec.id):
            errors.append("DUPLICATE_SKILL_ID")
        if not spec.steps:
            errors.append("EMPTY_COMPOSITE")
        if len(spec.steps) > self.max_steps:
            errors.append("TOO_MANY_STEPS")
        if not candidate.tests:
            errors.append("TESTS_REQUIRED")
        input_names = {item.name for item in spec.inputs}
        if len(input_names) != len(spec.inputs):
            errors.append("DUPLICATE_INPUT")
        for step in spec.steps:
            if step.skill_id == spec.id:
                errors.append("RECURSIVE_SKILL")
            if not self.registry.exists(step.skill_id):
                errors.append(f"UNKNOWN_STEP:{step.skill_id}")
            self._validate_references(step.arguments, input_names, errors)
        if len(spec.steps) == 1 and spec.steps[0].skill_id in {item.id for item in self.registry.list()}:
            errors.append(f"DUPLICATE_WRAPPER:{spec.steps[0].skill_id}")
        new_dependencies = sorted(set(spec.dependencies) - self.allowed_dependencies)
        if new_dependencies:
            notices.append("DEPENDENCY_APPROVAL_REQUIRED:" + ",".join(new_dependencies))
        if candidate.code:
            errors.extend(self._validate_code(candidate.code))
        return ValidationReport(not errors, tuple(dict.fromkeys(errors)), tuple(notices))

    @staticmethod
    def _validate_references(value, input_names: set[str], errors: list[str]) -> None:
        if isinstance(value, dict):
            if set(value) == {"$input"}:
                if value["$input"] not in input_names:
                    errors.append(f"UNKNOWN_INPUT_REFERENCE:{value['$input']}")
                return
            for item in value.values():
                SkillValidator._validate_references(item, input_names, errors)
        elif isinstance(value, list):
            for item in value:
                SkillValidator._validate_references(item, input_names, errors)

    @staticmethod
    def _validate_code(code: str) -> list[str]:
        errors: list[str] = []
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return ["INVALID_PYTHON"]
        aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for item in node.names:
                    aliases[item.asname or item.name] = item.name
                    if item.name.startswith(FORBIDDEN_IMPORT_PREFIXES):
                        errors.append(f"FORBIDDEN_IMPORT:{item.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.startswith(FORBIDDEN_IMPORT_PREFIXES):
                    errors.append(f"FORBIDDEN_IMPORT:{module}")
            elif isinstance(node, ast.Call):
                name = SkillValidator._call_name(node.func, aliases)
                if name in FORBIDDEN_CALLS or name.startswith("subprocess."):
                    errors.append(f"FORBIDDEN_CALL:{name}")
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and SECRET_PATTERNS.search(node.value):
                errors.append("SECRET_ACCESS_PATTERN")
            elif isinstance(node, ast.Attribute):
                name = SkillValidator._call_name(node, aliases)
                if name in {"os.environ", "os.getenv"}:
                    errors.append("SECRET_ENVIRONMENT_ACCESS")
        return list(dict.fromkeys(errors))

    @staticmethod
    def _call_name(node, aliases: dict[str, str]) -> str:
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            prefix = SkillValidator._call_name(node.value, aliases)
            return f"{prefix}.{node.attr}" if prefix else node.attr
        return ""

