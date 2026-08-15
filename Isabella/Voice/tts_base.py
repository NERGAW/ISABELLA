"""Provider-independent text-to-speech contracts."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class VoiceInfo:
    id: str
    name: str
    language: str
    gender: str
    provider: str


@dataclass(frozen=True)
class SynthesizedAudio:
    data: bytes
    audio_format: str
    voice: str
    synthesis_latency_ms: float
    time_to_first_audio_ms: float


class TTSProvider(ABC):
    name: str

    @abstractmethod
    def initialize(self) -> None:
        pass

    @abstractmethod
    def list_voices(self) -> list[VoiceInfo]:
        pass

    @abstractmethod
    def synthesize(self, text: str) -> SynthesizedAudio:
        pass

    @abstractmethod
    def speak(self, text: str) -> SynthesizedAudio | None:
        pass

    @abstractmethod
    def stop(self) -> None:
        pass

    @abstractmethod
    def health_check(self) -> bool:
        pass

    @abstractmethod
    def supports_streaming(self) -> bool:
        pass

    @abstractmethod
    def shutdown(self) -> None:
        pass
