"""ISABELLA Protocol v1 version negotiation constants."""

PROTOCOL_NAME = "ISABELLA Protocol"
PROTOCOL_VERSION = "1.0"
SUPPORTED_PROTOCOL_VERSIONS = frozenset({PROTOCOL_VERSION})


def is_compatible(version: str) -> bool:
    return version in SUPPORTED_PROTOCOL_VERSIONS

