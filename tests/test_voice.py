from collections import deque
from types import SimpleNamespace
import json
import threading
import time

import numpy as np
import pytest

from Isabella.Core.app import IsabellaApp, IsabellaStatus
from Isabella.Voice.audio import AudioDeviceError
from Isabella.Voice.listener import ListenerState, VoiceListener
from Isabella.Voice.models import TranscriptionResult, load_voice_config
from Isabella.Voice.stt import FasterWhisperSTT, STTError
from Isabella.Voice.wakeword import WakeWordDetector, normalize_transcription


VOICE_CONFIG = {
    "enabled": True,
    "language": "pt",
    "wake_word": "isabella",
    "wake_word_aliases": ["isabella", "isabela"],
    "model_size": "small",
    "device": "cpu",
    "compute_type": "int8",
    "input_device": None,
    "sample_rate": 16000,
    "vad_enabled": True,
    "command_timeout_seconds": 1,
    "speech_threshold": 0.015,
    "silence_duration_seconds": 0.1,
    "debug_transcription": True,
}


@pytest.mark.parametrize(
    ("text", "detected", "command"),
    [
        ("Isabella abra o Chrome", True, "abra o chrome"),
        ("Isabela abra o Chrome", True, "abra o chrome"),
        ("ISABELA, ABRA O CHROME", True, "abra o chrome"),
        ("Ei Isabella abra o Chrome", True, "abra o chrome"),
        ("Hoje Isabella está bonita", False, ""),
        ("Abra o Chrome", False, ""),
        ("isabel", False, ""),
    ],
)
def test_wake_word_cases(text, detected, command):
    result = WakeWordDetector(["isabella", "isabela"]).detect(text)

    assert result.wake_word_detected is detected
    assert result.command_text == command


def test_normalization_preserves_words():
    assert normalize_transcription("  ISABELA...   Abra o Chrome! ") == "isabela abra o chrome"


def test_voice_config(tmp_path):
    path = tmp_path / "voice.json"
    path.write_text(json.dumps(VOICE_CONFIG), encoding="utf-8")

    assert load_voice_config(path)["compute_type"] == "int8"


class FakeRecorder:
    def __init__(self, audio=None, error=None):
        self.audio = deque(audio or [])
        self.error = error

    def device_info(self):
        if self.error:
            raise self.error
        return {"name": "Fake microphone", "sample_rate": 16000}

    def capture_utterance(self, stop_event, pause_event=None):
        if self.audio:
            return self.audio.popleft()
        stop_event.wait(0.01)
        return np.empty(0, dtype=np.float32)


class FakeSTT:
    def __init__(self, texts=None, error=None):
        self.texts = deque(texts or [])
        self.error = error

    def transcribe(self, audio):
        if self.error:
            raise self.error
        text = self.texts.popleft() if self.texts else ""
        return TranscriptionResult(text, "pt", 0.9, 100.0, 10.0)


def wait_until(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_listener_start_stop_and_callback():
    commands = []
    audio = np.ones(1600, dtype=np.float32)
    listener = VoiceListener(
        VOICE_CONFIG,
        commands.append,
        recorder=FakeRecorder([audio]),
        stt=FakeSTT(["Isabela abra o Chrome"]),
    )

    assert listener.start() is True
    assert listener.start() is False
    assert wait_until(lambda: commands == ["abra o chrome"])
    assert listener.stop() is True
    assert listener.state == ListenerState.STOPPED


def test_listener_ignores_text_without_wake_word():
    commands = []
    listener = VoiceListener(
        VOICE_CONFIG,
        commands.append,
        recorder=FakeRecorder([np.ones(100, dtype=np.float32)]),
        stt=FakeSTT(["Hoje está muito quente"]),
    )

    listener.start()
    time.sleep(0.05)
    listener.stop()

    assert commands == []


def test_listener_queue_is_bounded():
    listener = VoiceListener(VOICE_CONFIG, lambda command: None, recorder=FakeRecorder(), stt=FakeSTT(), queue_size=1)

    assert listener.enqueue_audio(np.ones(1, dtype=np.float32)) is True
    assert listener.enqueue_audio(np.ones(1, dtype=np.float32)) is False


def test_listener_microphone_error():
    listener = VoiceListener(
        VOICE_CONFIG,
        lambda command: None,
        recorder=FakeRecorder(error=AudioDeviceError("invalid device")),
        stt=FakeSTT(),
    )

    listener.start()
    assert wait_until(lambda: listener.state == ListenerState.ERROR)
    assert listener.stop() is True


def test_listener_stt_error():
    listener = VoiceListener(
        VOICE_CONFIG,
        lambda command: None,
        recorder=FakeRecorder([np.ones(100, dtype=np.float32)]),
        stt=FakeSTT(error=STTError("load failed")),
    )

    listener.start()
    assert wait_until(lambda: listener.state == ListenerState.ERROR)
    assert listener.stop() is True


def test_stt_model_loads_only_once():
    factory_calls = []

    class FakeModel:
        def transcribe(self, audio, **kwargs):
            segment = SimpleNamespace(text=" Isabela teste ", avg_logprob=-0.1)
            return iter([segment]), SimpleNamespace(language="pt")

    def factory(*args, **kwargs):
        factory_calls.append((args, kwargs))
        return FakeModel()

    stt = FasterWhisperSTT(VOICE_CONFIG, model_factory=factory)
    audio = np.ones(1600, dtype=np.float32)

    stt.transcribe(audio)
    stt.transcribe(audio)

    assert stt.load_count == 1
    assert len(factory_calls) == 1


def test_core_remains_online_when_voice_configuration_is_invalid(tmp_path):
    voice_path = tmp_path / "voice.json"
    invalid = dict(VOICE_CONFIG, input_device="DOES_NOT_EXIST")
    voice_path.write_text(json.dumps(invalid), encoding="utf-8")
    app = IsabellaApp(log_path=tmp_path / "app.log")
    app.start()

    assert app.start_voice(lambda command: None, voice_path) is False
    assert app.status == IsabellaStatus.ONLINE
    app.shutdown()
