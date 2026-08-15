"""Natural neural TTS with an offline Windows fallback."""

from collections import OrderedDict
from io import BytesIO
import asyncio
import logging
from pathlib import Path
from queue import Empty, Full, Queue
import re
import tempfile
import threading
from time import perf_counter
from typing import Any, Callable

import av
import numpy as np
import sounddevice as sd

from .tts_base import SynthesizedAudio, TTSProvider, VoiceInfo


LOGGER = logging.getLogger("TTS")


class TTSError(RuntimeError):
    pass


class MemoryAudioPlayer:
    """Decode encoded audio in memory and play it through PortAudio."""

    def play(self, audio: SynthesizedAudio) -> float:
        started = perf_counter()
        samples: list[np.ndarray] = []
        sample_rate = 24000
        with av.open(BytesIO(audio.data)) as container:
            for frame in container.decode(audio=0):
                sample_rate = frame.sample_rate or sample_rate
                array = frame.to_ndarray()
                if array.ndim == 2:
                    array = array.mean(axis=0)
                if not np.issubdtype(array.dtype, np.floating):
                    maximum = max(abs(np.iinfo(array.dtype).min), np.iinfo(array.dtype).max)
                    array = array.astype(np.float32) / maximum
                samples.append(array.astype(np.float32, copy=False).reshape(-1))
        if not samples:
            raise TTSError("Synthesized audio contained no decodable frames")
        sd.play(np.concatenate(samples), sample_rate, blocking=True)
        return (perf_counter() - started) * 1000

    def stop(self) -> None:
        sd.stop()


class EdgeTTSProvider(TTSProvider):
    name = "edge"

    def __init__(self, config: dict[str, Any]) -> None:
        self.voice = config.get("voice") or "pt-BR-FranciscaNeural"
        self.rate = float(config.get("rate", 1.0))
        self.pitch = float(config.get("pitch", 0.0))
        self.volume = float(config.get("volume", 1.0))
        self._initialized = False

    def initialize(self) -> None:
        import edge_tts

        self._edge_tts = edge_tts
        self._initialized = True

    def list_voices(self) -> list[VoiceInfo]:
        if not self._initialized:
            self.initialize()
        voices = asyncio.run(self._edge_tts.list_voices())
        return [
            VoiceInfo(item["ShortName"], item["FriendlyName"], item["Locale"], item["Gender"], self.name)
            for item in voices
            if item.get("Locale") == "pt-BR" and item.get("Gender") == "Female"
        ]

    @staticmethod
    def _percent(value: float) -> str:
        return f"{round((value - 1.0) * 100):+d}%"

    def synthesize(self, text: str) -> SynthesizedAudio:
        if not self._initialized:
            self.initialize()
        communication = self._edge_tts.Communicate(
            text,
            self.voice,
            rate=self._percent(self.rate),
            volume=self._percent(self.volume),
            pitch=f"{round(self.pitch):+d}Hz",
        )
        started = perf_counter()
        first_audio: float | None = None
        chunks: list[bytes] = []
        for item in communication.stream_sync():
            if item["type"] == "audio":
                if first_audio is None:
                    first_audio = (perf_counter() - started) * 1000
                chunks.append(item["data"])
        synthesis_latency = (perf_counter() - started) * 1000
        if not chunks:
            raise TTSError("Edge TTS returned no audio")
        return SynthesizedAudio(
            b"".join(chunks),
            "mp3",
            self.voice,
            synthesis_latency,
            first_audio or synthesis_latency,
        )

    def speak(self, text: str) -> SynthesizedAudio:
        audio = self.synthesize(text)
        MemoryAudioPlayer().play(audio)
        return audio

    def stop(self) -> None:
        sd.stop()

    def health_check(self) -> bool:
        return self._initialized

    def supports_streaming(self) -> bool:
        return True

    def shutdown(self) -> None:
        self.stop()
        self._initialized = False


class SAPIProvider(TTSProvider):
    name = "sapi"
    direct_playback = True

    def __init__(self, config: dict[str, Any]) -> None:
        self.voice_name = config.get("fallback_voice") or "Microsoft Maria Desktop"
        self.rate = float(config.get("rate", 1.0))
        self.volume = float(config.get("volume", 1.0))
        self._engine = None

    def initialize(self) -> None:
        import pyttsx3

        self._engine = pyttsx3.init("sapi5")
        self._engine.setProperty("rate", round(180 * self.rate))
        self._engine.setProperty("volume", min(max(self.volume, 0.0), 1.0))
        for voice in self._engine.getProperty("voices"):
            if self.voice_name.casefold() in voice.name.casefold() or "maria" in voice.name.casefold():
                self._engine.setProperty("voice", voice.id)
                self.voice_name = voice.name
                break

    def list_voices(self) -> list[VoiceInfo]:
        if self._engine is None:
            self.initialize()
        return [
            VoiceInfo(voice.id, voice.name, ",".join(voice.languages), str(voice.gender), self.name)
            for voice in self._engine.getProperty("voices")
        ]

    def synthesize(self, text: str) -> SynthesizedAudio:
        if self._engine is None:
            self.initialize()
        descriptor, filename = tempfile.mkstemp(suffix=".wav")
        try:
            import os

            os.close(descriptor)
            Path(filename).unlink(missing_ok=True)
            started = perf_counter()
            self._engine.save_to_file(text, filename)
            self._engine.runAndWait()
            latency = (perf_counter() - started) * 1000
            data = Path(filename).read_bytes()
            if not data:
                raise TTSError("SAPI returned no audio")
            return SynthesizedAudio(data, "wav", self.voice_name, latency, latency)
        finally:
            Path(filename).unlink(missing_ok=True)

    def speak(self, text: str) -> SynthesizedAudio:
        if self._engine is None:
            self.initialize()
        started = perf_counter()
        first_audio: list[float] = []

        def on_started(name) -> None:
            if not first_audio:
                first_audio.append((perf_counter() - started) * 1000)

        token = self._engine.connect("started-utterance", on_started)
        self._engine.say(text)
        self._engine.runAndWait()
        self._engine.disconnect(token)
        total = (perf_counter() - started) * 1000
        return SynthesizedAudio(b"", "direct", self.voice_name, 0.0, first_audio[0] if first_audio else total)

    def stop(self) -> None:
        if self._engine is not None:
            self._engine.stop()

    def health_check(self) -> bool:
        return self._engine is not None

    def supports_streaming(self) -> bool:
        return False

    def shutdown(self) -> None:
        self.stop()
        self._engine = None


PRONUNCIATIONS = {
    "ISABELLA": "Isabela",
    "WhatsApp": "Uótis Ép",
    "GitHub": "Guite Rãb",
    "Ollama": "Olama",
    "Faster Whisper": "Fáster Uísper",
    "CPU": "cê pê u",
    "GPU": "gê pê u",
    "RAM": "rãm",
    "USB": "u ésse bê",
    "GPS": "gê pê ésse",
    "HUD": "agá u dê",
    "IA": "i a",
}


def prepare_for_speech(text: str) -> str:
    spoken = text
    for source, replacement in PRONUNCIATIONS.items():
        spoken = re.sub(rf"\b{re.escape(source)}\b", replacement, spoken, flags=re.IGNORECASE)
    return spoken


class TTSManager:
    def __init__(
        self,
        config: dict[str, Any],
        primary: TTSProvider | None = None,
        fallback: TTSProvider | None = None,
        player: MemoryAudioPlayer | None = None,
        on_speaking_change: Callable[[bool], None] | None = None,
    ) -> None:
        self.config = config
        self.primary = primary or EdgeTTSProvider(config)
        self.fallback = fallback or SAPIProvider(config)
        self.player = player or MemoryAudioPlayer()
        self.on_speaking_change = on_speaking_change or (lambda speaking: None)
        self._queue: Queue[str | None] = Queue(maxsize=int(config.get("queue_max_size", 10)))
        self._cache: OrderedDict[str, SynthesizedAudio] = OrderedDict()
        self._cache_max = int(config.get("cache_max_entries", 32))
        self._cache_enabled = bool(config.get("cache_enabled", True))
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._active_provider: TTSProvider | None = None
        self.state = "STOPPED"
        self.metrics: list[dict[str, float | str]] = []

    def initialize(self, timeout: float = 5.0) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        self._stop_event.clear()
        self._ready_event.clear()
        self._thread = threading.Thread(target=self._worker, name="IsabellaTTS", daemon=True)
        self._thread.start()
        self._ready_event.wait(timeout)
        return self.state != "ERROR"

    def _worker(self) -> None:
        initialized = 0
        for provider in (self.primary, self.fallback):
            if provider is None:
                continue
            try:
                started = perf_counter()
                provider.initialize()
                LOGGER.info("provider=%s initialization_ms=%.2f", provider.name, (perf_counter() - started) * 1000)
                initialized += 1
            except Exception as exc:
                LOGGER.warning("provider=%s initialization failed error=%s", provider.name, exc)
        self.state = "READY" if initialized else "ERROR"
        self._ready_event.set()
        while not self._stop_event.is_set() or not self._queue.empty():
            try:
                text = self._queue.get(timeout=0.2)
            except Empty:
                continue
            if text is None:
                self._queue.task_done()
                break
            self.state = "SPEAKING"
            self.on_speaking_change(True)
            try:
                self._speak_with_fallback(text)
                if self.state != "ERROR":
                    self.state = "READY"
            finally:
                self.on_speaking_change(False)
                self._queue.task_done()
        if self.state != "ERROR":
            self.state = "STOPPED"
        for provider in (self.primary, self.fallback):
            if provider:
                provider.shutdown()

    def _speak_with_fallback(self, text: str) -> None:
        errors = 0
        for provider in (self.primary, self.fallback):
            if provider is None or not provider.health_check():
                continue
            try:
                self._active_provider = provider
                self._speak_provider(provider, text)
                return
            except Exception as exc:
                errors += 1
                LOGGER.warning("provider=%s speech failed error=%s", provider.name, exc)
        self.state = "ERROR"
        if errors == 0:
            LOGGER.error("No healthy TTS provider is available")

    def _speak_provider(self, provider: TTSProvider, text: str) -> None:
        if getattr(provider, "direct_playback", False):
            started = perf_counter()
            audio = provider.speak(text)
            speech_time = (perf_counter() - started) * 1000
            self.metrics.append(
                {
                    "provider": provider.name,
                    "synthesis_latency_ms": 0.0,
                    "time_to_first_audio_ms": audio.time_to_first_audio_ms if audio else speech_time,
                    "total_speech_time_ms": speech_time,
                    "queue_to_finish_ms": speech_time,
                }
            )
            LOGGER.info(
                "provider=%s synthesis_latency_ms=0.00 time_to_first_audio_ms=%.2f total_speech_time_ms=%.2f cache_hit=false",
                provider.name,
                audio.time_to_first_audio_ms if audio else speech_time,
                speech_time,
            )
            return
        cache_key = f"{provider.name}:{getattr(provider, 'voice', getattr(provider, 'voice_name', ''))}:{text}"
        audio = self._cache.get(cache_key)
        cache_hit = audio is not None
        if audio is None:
            audio = provider.synthesize(text)
            if self._cache_enabled and len(text) <= 100:
                self._cache[cache_key] = audio
                self._cache.move_to_end(cache_key)
                while len(self._cache) > self._cache_max:
                    self._cache.popitem(last=False)
        playback_started = perf_counter()
        speech_time = self.player.play(audio)
        self.metrics.append(
            {
                "provider": provider.name,
                "synthesis_latency_ms": 0.0 if cache_hit else audio.synthesis_latency_ms,
                "time_to_first_audio_ms": 0.0 if cache_hit else audio.time_to_first_audio_ms,
                "total_speech_time_ms": speech_time,
                "queue_to_finish_ms": (perf_counter() - playback_started) * 1000,
            }
        )
        LOGGER.info(
            "provider=%s synthesis_latency_ms=%.2f time_to_first_audio_ms=%.2f total_speech_time_ms=%.2f cache_hit=%s",
            provider.name,
            0.0 if cache_hit else audio.synthesis_latency_ms,
            0.0 if cache_hit else audio.time_to_first_audio_ms,
            speech_time,
            cache_hit,
        )

    def speak(self, text: str) -> bool:
        if not isinstance(text, str) or not text.strip() or self.state == "ERROR":
            return False
        spoken = prepare_for_speech(text.strip())
        try:
            self._queue.put_nowait(spoken)
            return True
        except Full:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
                self._queue.put_nowait(spoken)
                LOGGER.warning("TTS queue full; dropped oldest response")
                return True
            except (Empty, Full):
                return False

    def stop(self) -> None:
        for provider in (self.primary, self.fallback):
            if provider and provider.health_check():
                provider.stop()
        self.player.stop()
        while True:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except Empty:
                break

    def health_check(self) -> bool:
        return any(provider and provider.health_check() for provider in (self.primary, self.fallback))

    def switch_provider(self) -> None:
        self.primary, self.fallback = self.fallback, self.primary

    def shutdown(self, timeout: float = 5.0) -> bool:
        self._stop_event.set()
        self.stop()
        try:
            self._queue.put_nowait(None)
        except Full:
            pass
        if self._thread:
            self._thread.join(timeout)
        return not bool(self._thread and self._thread.is_alive())
