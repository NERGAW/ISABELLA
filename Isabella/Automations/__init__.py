"""Public event automations API."""

from .conditions import all_match, matches, resolve_field
from .engine import AutomationEngine
from .manager import AutomationManager, load_automations_config
from .models import (
    Automation, AutomationAction, AutomationCondition, AutomationRun,
    AutomationTrigger, ConditionOperator, TriggerType,
)
from .storage import AutomationStorage

__all__ = [
    "Automation", "AutomationAction", "AutomationCondition", "AutomationEngine",
    "AutomationManager", "AutomationRun", "AutomationStorage", "AutomationTrigger",
    "ConditionOperator", "TriggerType", "all_match", "load_automations_config",
    "matches", "resolve_field",
]

