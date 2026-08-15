"""OpenCV webcam access that opens only for explicit on-demand capture."""

from __future__ import annotations

import os
import tempfile
from collections import deque
from pathlib import Path
from time import perf_counter

from .models import ImageCapture, VisionSource, now_iso


class CameraError(RuntimeError):
    pass


class CameraCapture:
    def __init__(self, device: int | None = None, max_width: int = 1920, max_height: int = 1080, backend=None) -> None:
        self.device = 0 if device is None else int(device)
        self.max_width = max_width
        self.max_height = max_height
        self._backend = backend
        self._capture = None
        self.latencies_ms: deque[float] = deque(maxlen=200)

    @property
    def backend(self):
        if self._backend is None:
            import cv2

            self._backend = cv2
        return self._backend

    def list_cameras(self, maximum: int = 5) -> list[int]:
        available = []
        for index in range(maximum):
            capture = self._new_capture(index)
            try:
                if capture.isOpened():
                    available.append(index)
            finally:
                capture.release()
        return available

    def open(self) -> bool:
        if self._capture is not None and self._capture.isOpened():
            return True
        self._capture = self._new_capture(self.device)
        return bool(self._capture.isOpened())

    def _new_capture(self, device: int):
        preferred = getattr(self.backend, "CAP_DSHOW", None)
        if preferred is not None:
            try:
                capture = self.backend.VideoCapture(device, preferred)
                if capture.isOpened():
                    return capture
                capture.release()
            except Exception:
                pass
        return self.backend.VideoCapture(device)

    def capture_frame(self, destination: Path | None = None, temporary: bool = True) -> ImageCapture:
        started = perf_counter()
        try:
            if not self.open():
                raise CameraError("Camera is unavailable")
            success, frame = self._capture.read()
            if not success or frame is None:
                raise CameraError("Camera returned no frame")
            height, width = frame.shape[:2]
            scale = min(1.0, self.max_width / width, self.max_height / height)
            if scale < 1.0:
                frame = self.backend.resize(frame, (round(width * scale), round(height * scale)))
                height, width = frame.shape[:2]
            path = destination or self._temporary_path(".jpg")
            path.parent.mkdir(parents=True, exist_ok=True)
            if not self.backend.imwrite(str(path), frame):
                if destination is None:
                    path.unlink(missing_ok=True)
                raise CameraError("Unable to save camera frame")
            return ImageCapture(
                VisionSource.CAMERA, now_iso(), width, height, path=path,
                metadata={"camera_device": self.device}, temporary=temporary,
            )
        finally:
            self.latencies_ms.append((perf_counter() - started) * 1000)

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None

    def health_check(self) -> bool:
        try:
            return self.open()
        except Exception:
            return False
        finally:
            self.close()

    @staticmethod
    def _temporary_path(suffix: str) -> Path:
        descriptor, name = tempfile.mkstemp(prefix="isabella_camera_", suffix=suffix)
        os.close(descriptor)
        return Path(name)
