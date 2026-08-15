"""Lightweight presentation models for the HUD."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class UIState(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    TRANSCRIBING = "TRANSCRIBING"
    THINKING = "THINKING"
    SEARCHING = "SEARCHING"
    VISION_ANALYZING = "VISION ANALYZING"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    SPEAKING = "SPEAKING"
    ERROR = "ERROR"


class MessageRole(str, Enum):
    USER = "USER"
    ISABELLA = "ISABELLA"
    SYSTEM = "SYSTEM"
    ERROR = "ERROR"


class MessageType(str, Enum):
    TEXT = "TEXT"
    STATUS = "STATUS"
    ACTION = "ACTION"
    ERROR = "ERROR"


@dataclass(frozen=True)
class UIMessage:
    role: MessageRole
    text: str
    timestamp: datetime = field(default_factory=datetime.now)
    type: MessageType = MessageType.TEXT


SUBSYSTEMS = ("CORE", "LLM", "MEMORY", "CONTEXT", "VISION", "RESEARCH", "VOICE INPUT", "VOICE OUTPUT", "SKILLS", "PLANNER")
