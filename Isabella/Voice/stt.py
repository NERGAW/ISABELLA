"""Lazy-loaded Faster Whisper speech-to-text provider."""

import logging
import math
import threading
from time import perf_counter
from typing import Any

import numpy as np

from .models import TranscriptionResult, load_voice_config


LOGGER = logging.getLogger("STT")


class STTError(RuntimeError):
    pass


class FasterWhisperSTT:
    def __init__(self, config: dict[str, Any], model_factory=None) -> None:
        self.config = config
        self._model_factory = model_factory
        self._model = None
        self._load_lock = threading.Lock()
        self.load_count = 0
        self.latencies_ms: list[float] = []

    def load(self) -> None:
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            try:
                if self._model_factory is None:
                    from faster_whisper import WhisperModel

                    factory = WhisperModel
                else:
                    factory = self._model_factory
                logging.getLogger("faster_whisper").setLevel(logging.WARNING)
                self._model = factory(
                    self.config["model_size"],
                    device=self.config["device"],
                    compute_type=self.config["compute_type"],
                )
                self.load_count += 1
                LOGGER.info(
                    "model=%s device=%s compute_type=%s loaded=true",
                    self.config["model_size"],
                    self.config["device"],
                    self.config["compute_type"],
                )
            except Exception as exc:
                raise STTError(f"Unable to load Faster Whisper: {exc}") from exc

    def transcribe(self, audio: np.ndarray) -> TranscriptionResult:
        if not isinstance(audio, np.ndarray) or audio.ndim != 1:
            raise STTError("Audio must be a mono numpy array")
        if audio.size == 0:
            return TranscriptionResult("", self.config["language"], None, 0.0, 0.0)
        self.load()
        started = perf_counter()
        try:
            segments_generator, info = self._model.transcribe(
                audio,
                language=self.config["language"],
                beam_size=1,
                best_of=1,
                vad_filter=bool(self.config["vad_enabled"]),
                vad_parameters={"min_silence_duration_ms": 500},
                condition_on_previous_text=False,
                hotwords="Isabella Isabela",
            )
            segments = list(segments_generator)
        except Exception as exc:
            raise STTError(f"Transcription failed: {exc}") from exc
        latency = (perf_counter() - started) * 1000
        text = " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()
        probabilities = [math.exp(segment.avg_logprob) for segment in segments]
        confidence = sum(probabilities) / len(probabilities) if probabilities else None
        duration = audio.size / float(self.config["sample_rate"]) * 1000
        self.latencies_ms.append(latency)
        if self.config["debug_transcription"]:
            LOGGER.info('raw=%r latency_ms=%.2f', text, latency)
        return TranscriptionResult(text, getattr(info, "language", self.config["language"]), confidence, duration, latency)

    @property
    def average_latency_ms(self) -> float:
        return sum(self.latencies_ms) / len(self.latencies_ms) if self.latencies_ms else 0.0


def main() -> None:
    import argparse
    from .audio import AudioRecorder

    parser = argparse.ArgumentParser(description="ISABELLA isolated STT utility")
    parser.add_argument("--compare-base", action="store_true", help="Transcribe the same recording with small and base")
    arguments = parser.parse_args()
    config = load_voice_config()
    recorder = AudioRecorder(config)
    print(f"Microfone: {recorder.device_info()['name']}", flush=True)
    print("Gravando por 6 segundos. Fale agora...", flush=True)
    audio = recorder.record_duration(6.0)
    print("Gravação concluída. Transcrevendo...", flush=True)
    model_names = [config["model_size"]]
    if arguments.compare_base and "base" not in model_names:
        model_names.append("base")
    for model_name in model_names:
        model_config = dict(config, model_size=model_name)
        result = FasterWhisperSTT(model_config).transcribe(audio)
        print(f"Modelo: {model_name}")
        print(f"Texto: {result.text}")
        print(f"Latência: {result.latency_ms:.2f} ms")


if __name__ == "__main__":
    main()
