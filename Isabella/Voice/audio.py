"""Microphone discovery and bounded utterance capture."""

from collections import deque
import logging
from queue import Empty, Full, Queue
import threading
from time import monotonic
from typing import Any

import numpy as np
import sounddevice as sd


LOGGER = logging.getLogger("MIC")


class AudioDeviceError(RuntimeError):
    pass


def list_input_devices() -> list[dict[str, Any]]:
    devices = []
    for index, device in enumerate(sd.query_devices()):
        if int(device["max_input_channels"]) > 0:
            devices.append(
                {
                    "id": index,
                    "name": str(device["name"]),
                    "input_channels": int(device["max_input_channels"]),
                    "sample_rate": int(device["default_samplerate"]),
                }
            )
    return devices


class AudioRecorder:
    def __init__(self, config: dict[str, Any]) -> None:
        self.sample_rate = int(config["sample_rate"])
        self.device = self._resolve_device(config.get("input_device"))
        self.timeout = float(config["command_timeout_seconds"])
        self.speech_threshold = float(config.get("speech_threshold", 0.015))
        self.silence_duration = float(config.get("silence_duration_seconds", 0.8))

    @staticmethod
    def _resolve_device(selection: int | str | None) -> int | str | None:
        if selection is None or isinstance(selection, int):
            return selection
        selection_text = selection.casefold()
        matches = [device for device in list_input_devices() if selection_text in device["name"].casefold()]
        if not matches:
            raise AudioDeviceError(f"Input device not found: {selection}")
        return matches[0]["id"]

    def device_info(self) -> dict[str, Any]:
        try:
            info = sd.query_devices(self.device, "input")
        except sd.PortAudioError as exc:
            raise AudioDeviceError(f"Input device unavailable: {exc}") from exc
        return {
            "id": self.device if self.device is not None else sd.default.device[0],
            "name": str(info["name"]),
            "input_channels": int(info["max_input_channels"]),
            "sample_rate": self.sample_rate,
        }

    def capture_utterance(
        self,
        stop_event: threading.Event,
        pause_event: threading.Event | None = None,
    ) -> np.ndarray:
        block_duration = 0.1
        blocksize = int(self.sample_rate * block_duration)
        blocks: Queue[np.ndarray] = Queue(maxsize=32)

        def callback(indata, frames, time_info, status) -> None:
            if status:
                LOGGER.warning("audio_status=%s", status)
            try:
                blocks.put_nowait(indata[:, 0].astype(np.float32, copy=True))
            except Full:
                LOGGER.warning("Audio buffer full; dropping block")

        captured: list[np.ndarray] = []
        pre_roll: deque[np.ndarray] = deque(maxlen=3)
        speech_started = False
        last_speech = monotonic()
        started = monotonic()
        try:
            with sd.InputStream(
                device=self.device,
                channels=1,
                samplerate=self.sample_rate,
                dtype="float32",
                blocksize=blocksize,
                callback=callback,
            ):
                LOGGER.info("device=%s", self.device_info()["name"])
                while (
                    not stop_event.is_set()
                    and not (pause_event and pause_event.is_set())
                    and monotonic() - started < self.timeout
                ):
                    try:
                        block = blocks.get(timeout=0.2)
                    except Empty:
                        continue
                    energy = float(np.sqrt(np.mean(np.square(block))))
                    if energy >= self.speech_threshold:
                        if not speech_started:
                            captured.extend(pre_roll)
                            speech_started = True
                            LOGGER.info("speech detected")
                        last_speech = monotonic()
                    if speech_started:
                        captured.append(block)
                        if monotonic() - last_speech >= self.silence_duration:
                            break
                    else:
                        pre_roll.append(block)
        except sd.PortAudioError as exc:
            raise AudioDeviceError(f"Unable to capture microphone audio: {exc}") from exc
        return np.concatenate(captured).astype(np.float32) if captured else np.empty(0, dtype=np.float32)

    def record_duration(self, seconds: float) -> np.ndarray:
        """Record a fixed diagnostic sample without input energy gating."""
        if seconds <= 0:
            raise ValueError("Recording duration must be positive")
        try:
            recording = sd.rec(
                int(seconds * self.sample_rate),
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                device=self.device,
            )
            sd.wait()
        except sd.PortAudioError as exc:
            raise AudioDeviceError(f"Unable to record microphone audio: {exc}") from exc
        return recording[:, 0].astype(np.float32, copy=False)


def _print_devices() -> None:
    print("ID | Nome | Canais de entrada | Sample rate")
    for device in list_input_devices():
        print(f"{device['id']} | {device['name']} | {device['input_channels']} | {device['sample_rate']}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="ISABELLA microphone utility")
    parser.add_argument("--list-devices", action="store_true")
    arguments = parser.parse_args()
    if arguments.list_devices:
        _print_devices()
    else:
        parser.print_help()
