"""Small condition evaluator; deliberately not a programming language."""

from __future__ import annotations

from typing import Any

from .models import AutomationCondition, ConditionOperator


MISSING = object()


def resolve_field(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return MISSING
        current = current[part]
    return current


def matches(condition: AutomationCondition, payload: dict[str, Any]) -> bool:
    actual = resolve_field(payload, condition.field)
    operator = condition.operator
    if operator is ConditionOperator.EXISTS:
        return (actual is not MISSING) is bool(condition.value if condition.value is not None else True)
    if actual is MISSING:
        return False
    if operator is ConditionOperator.EQUALS:
        return actual == condition.value
    if operator is ConditionOperator.NOT_EQUALS:
        return actual != condition.value
    if operator is ConditionOperator.CONTAINS:
        try:
            return condition.value in actual
        except TypeError:
            return False
    if operator is ConditionOperator.GREATER_THAN:
        try:
            return actual > condition.value
        except TypeError:
            return False
    if operator is ConditionOperator.LESS_THAN:
        try:
            return actual < condition.value
        except TypeError:
            return False
    return False


def all_match(conditions: tuple[AutomationCondition, ...], payload: dict[str, Any]) -> bool:
    return all(matches(item, payload) for item in conditions)

