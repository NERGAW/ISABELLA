from datetime import datetime, timezone


def now_iso(): return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def age_seconds(timestamp: str | None, now=None) -> float:
    if not timestamp: return float("inf")
    current=now or datetime.now(timezone.utc); observed=datetime.fromisoformat(timestamp)
    if observed.tzinfo is None: observed=observed.replace(tzinfo=timezone.utc)
    return max(0.0,(current-observed).total_seconds())
