"""Permissions reserved for current and future policy rules."""

from enum import Enum


class Permission(str, Enum):
    READ = "READ"
    WRITE = "WRITE"
    DELETE = "DELETE"


SENSITIVE_FUTURE_PERMISSIONS = frozenset({Permission.WRITE, Permission.DELETE})

