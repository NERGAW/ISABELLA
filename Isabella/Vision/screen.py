"""Common Pillow-based capture used by Vision and system.screenshot."""

from __future__ import annotations

import ctypes
import os
import tempfile
from collections import deque
from ctypes import wintypes
from pathlib import Path
from time import perf_counter

from PIL import Image, ImageGrab

from Isabella.Context.providers import WindowsContextProvider
from .models import ImageCapture, VisionSource, now_iso


class ScreenCaptureError(RuntimeError):
    pass


class ScreenCapturer:
    def __init__(self, max_width: int = 1920, max_height: int = 1080, grabber=None, window_provider=None) -> None:
        self.max_width = max_width
        self.max_height = max_height
        self.grabber = grabber or ImageGrab.grab
        self.window_provider = window_provider or WindowsContextProvider()
        self.latencies_ms: deque[float] = deque(maxlen=200)

    @staticmethod
    def monitor_bounds() -> list[tuple[int, int, int, int]]:
        bounds: list[tuple[int, int, int, int]] = []
        try:
            callback_type = ctypes.WINFUNCTYPE(
                ctypes.c_int, wintypes.HMONITOR, wintypes.HDC,
                ctypes.POINTER(wintypes.RECT), wintypes.LPARAM,
            )

            def collect(monitor, device_context, rect, data):
                value = rect.contents
                bounds.append((value.left, value.top, value.right, value.bottom))
                return 1

            ctypes.windll.user32.EnumDisplayMonitors(None, None, callback_type(collect), 0)
        except (AttributeError, OSError):
            return []
        return bounds

    @staticmethod
    def active_window_bounds() -> tuple[int, int, int, int] | None:
        try:
            user32 = ctypes.windll.user32
            user32.GetForegroundWindow.restype = wintypes.HWND
            handle = user32.GetForegroundWindow()
            rect = wintypes.RECT()
            if not handle or not user32.GetWindowRect(handle, ctypes.byref(rect)):
                return None
            if rect.right <= rect.left or rect.bottom <= rect.top:
                return None
            return rect.left, rect.top, rect.right, rect.bottom
        except (AttributeError, OSError):
            return None

    def capture_screen(
        self, screen_index: int = 0, destination: Path | None = None, temporary: bool = True,
    ) -> ImageCapture:
        monitors = self.monitor_bounds()
        if screen_index < 0 or (monitors and screen_index >= len(monitors)):
            raise ScreenCaptureError(f"Screen {screen_index} is unavailable")
        bbox = monitors[screen_index] if monitors else None
        return self._capture(VisionSource.SCREEN, bbox, destination, temporary, {"screen_index": screen_index})

    def capture_active_window(self, destination: Path | None = None, temporary: bool = True) -> ImageCapture:
        bounds = self.active_window_bounds()
        if bounds is None:
            raise ScreenCaptureError("Active window is unavailable")
        window = self.window_provider.active_window()
        return self._capture(
            VisionSource.ACTIVE_WINDOW, bounds, destination, temporary,
            {"application": window.application}, window.title,
        )

    def _capture(
        self, source: VisionSource, bbox, destination: Path | None, temporary: bool,
        metadata: dict, active_window: str | None = None,
    ) -> ImageCapture:
        started = perf_counter()
        try:
            image = self.grabber(bbox=bbox)
            if not isinstance(image, Image.Image):
                raise ScreenCaptureError("Screen provider returned an invalid image")
            image.thumbnail((self.max_width, self.max_height), Image.Resampling.LANCZOS)
            path = destination or self._temporary_path(".png")
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                image.save(path, format="PNG")
            except Exception:
                if destination is None:
                    path.unlink(missing_ok=True)
                raise
            return ImageCapture(
                source, now_iso(), image.width, image.height, path=path,
                active_window=active_window, metadata=metadata, temporary=temporary,
            )
        except ScreenCaptureError:
            raise
        except Exception as exc:
            raise ScreenCaptureError(f"Unable to capture {source.value.lower()}: {exc}") from exc
        finally:
            self.latencies_ms.append((perf_counter() - started) * 1000)

    @staticmethod
    def _temporary_path(suffix: str) -> Path:
        descriptor, name = tempfile.mkstemp(prefix="isabella_vision_", suffix=suffix)
        os.close(descriptor)
        return Path(name)

    @staticmethod
    def health_check() -> bool:
        return hasattr(ImageGrab, "grab")
