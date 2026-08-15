"""Non-blocking voice capture and processing service."""

from enum import Enum
import logging
from queue import Empty, Full, Queue
import threading
from time import perf_counter
from typing import Callable

import numpy as np

from .audio import AudioDeviceError, AudioRecorder
from .models import load_voice_config
from .stt import FasterWhisperSTT, STTError
from .wakeword import WakeWordDetector


LOGGER = logging.getLogger("VOICE")


class ListenerState(str, Enum):
    STOPPED = "STOPPED"
    LISTENING = "LISTENING"
    TRANSCRIBING = "TRANSCRIBING"
    PROCESSING = "PROCESSING"
    ERROR = "ERROR"


class VoiceListener:
    def __init__(
        self,
        config: dict,
        callback: Callable[[str], object],
        recorder=None,
        stt=None,
        queue_size: int = 2,
    ) -> None:
        self.config = config
        self.callback = callback
        self.recorder = recorder or AudioRecorder(config)
        self.stt = stt or FasterWhisperSTT(config)
        self.wakeword = WakeWordDetector(config["wake_word_aliases"])
        self.state = ListenerState.STOPPED
        self._stop_event = threading.Event()
        self._audio_queue: Queue[tuple[np.ndarray, float]] = Queue(maxsize=queue_size)
        self._capture_thread: threading.Thread | None = None
        self._processing_thread: threading.Thread | None = None
        self.total_latencies_ms: list[float] = []

    @classmethod
    def from_config(cls, callback: Callable[[str], object]) -> "VoiceListener":
        return cls(load_voice_config(), callback)

    def start(self) -> bool:
        if self.is_running:
            return False
        self._stop_event.clear()
        self.state = ListenerState.LISTENING
        self._capture_thread = threading.Thread(target=self._capture_loop, name="IsabellaVoiceCapture", daemon=True)
        self._processing_thread = threading.Thread(target=self._processing_loop, name="IsabellaVoiceProcessing", daemon=True)
        self._capture_thread.start()
        self._processing_thread.start()
        return True

    @property
    def is_running(self) -> bool:
        return bool(self._capture_thread and self._capture_thread.is_alive())

    def enqueue_audio(self, audio: np.ndarray) -> bool:
        try:
            audio_duration = audio.size / float(self.config["sample_rate"])
            capture_started = perf_counter() - audio_duration
            self._audio_queue.put_nowait((audio, capture_started))
            return True
        except Full:
            LOGGER.warning("Voice queue full; dropping utterance")
            return False

    def _capture_loop(self) -> None:
        try:
            info = self.recorder.device_info()
            LOGGER.info("device=%s sample_rate=%s", info["name"], info["sample_rate"])
            while not self._stop_event.is_set():
                self.state = ListenerState.LISTENING
                audio = self.recorder.capture_utterance(self._stop_event)
                if audio.size:
                    self.enqueue_audio(audio)
        except AudioDeviceError:
            self.state = ListenerState.ERROR
            LOGGER.exception("Voice input disabled because the microphone is unavailable")
            self._stop_event.set()
        except Exception:
            self.state = ListenerState.ERROR
            LOGGER.exception("Unexpected microphone failure")
            self._stop_event.set()

    def _processing_loop(self) -> None:
        while not self._stop_event.is_set() or not self._audio_queue.empty():
            try:
                audio, capture_started = self._audio_queue.get(timeout=0.2)
            except Empty:
                continue
            try:
                self.state = ListenerState.TRANSCRIBING
                transcription = self.stt.transcribe(audio)
                if not transcription.text:
                    continue
                wake_started = perf_counter()
                command = self.wakeword.detect(transcription.text)
                wake_latency = (perf_counter() - wake_started) * 1000
                LOGGER.info(
                    "wake_detected=%s alias=%s latency_ms=%.3f",
                    command.wake_word_detected,
                    command.wake_alias,
                    wake_latency,
                )
                if not command.wake_word_detected or not command.command_text:
                    continue
                LOGGER.info("command=%r", command.command_text)
                self.state = ListenerState.PROCESSING
                LOGGER.info("forwarding command to Brain")
                brain_started = perf_counter()
                self.callback(command.command_text)
                brain_latency = (perf_counter() - brain_started) * 1000
                total_latency = (perf_counter() - capture_started) * 1000
                self.total_latencies_ms.append(total_latency)
                LOGGER.info(
                    "brain_latency_ms=%.2f audio_capture_to_end_ms=%.2f total_voice_command_ms=%.2f",
                    brain_latency,
                    total_latency,
                    total_latency,
                )
            except STTError:
                self.state = ListenerState.ERROR
                LOGGER.exception("Voice input disabled because STT failed")
                self._stop_event.set()
            except Exception:
                LOGGER.exception("Voice command processing failed")
            finally:
                self._audio_queue.task_done()
        if self.state != ListenerState.ERROR:
            self.state = ListenerState.STOPPED

    def stop(self, timeout: float = 3.0) -> bool:
        self._stop_event.set()
        for thread in (self._capture_thread, self._processing_thread):
            if thread and thread is not threading.current_thread():
                thread.join(timeout=timeout)
        if self.state != ListenerState.ERROR:
            self.state = ListenerState.STOPPED
        return not any(thread and thread.is_alive() for thread in (self._capture_thread, self._processing_thread))

    @property
    def average_total_latency_ms(self) -> float:
        return sum(self.total_latencies_ms) / len(self.total_latencies_ms) if self.total_latencies_ms else 0.0
