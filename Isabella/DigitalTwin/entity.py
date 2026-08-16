import re


def twin_id(value: str) -> str:
    normalized=re.sub(r"[^A-Z0-9_.-]+","_",str(value).strip().upper()).strip("_")
    if not normalized: raise ValueError("Twin id is required")
    return normalized
