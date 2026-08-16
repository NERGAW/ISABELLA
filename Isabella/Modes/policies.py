"""Safety invariants which modes are never allowed to weaken."""

ALLOWED_NETWORK_POLICIES = frozenset({"standard", "local_preferred", "local_only"})
ALLOWED_DIAGNOSTICS_LEVELS = frozenset({"standard", "detailed", "minimal"})


def validate_safety(mode) -> None:
    if mode.network_policy not in ALLOWED_NETWORK_POLICIES:
        raise ValueError(f"Invalid network policy for mode {mode.id}")
    if mode.diagnostics_level not in ALLOWED_DIAGNOSTICS_LEVELS:
        raise ValueError(f"Invalid diagnostics level for mode {mode.id}")
    forbidden = {"security.*", "system.confirm_critical"}
    if forbidden.intersection(mode.disabled_skills):
        raise ValueError("Modes cannot disable Security or critical confirmation")
