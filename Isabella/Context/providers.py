"""Small best-effort providers for real Windows operational state."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

import psutil


@dataclass(frozen=True)
class ActiveWindow:
    application: str
    title: str
    available: bool = True


class WindowsContextProvider:
    """Query Windows only when requested; no background polling."""

    def active_window(self) -> ActiveWindow:
        try:
            user32 = ctypes.windll.user32
            user32.GetForegroundWindow.restype = wintypes.HWND
            user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
            user32.GetWindowTextLengthW.restype = ctypes.c_int
            user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
            user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            handle = user32.GetForegroundWindow()
            if not handle:
                return ActiveWindow("unavailable", "unavailable", False)
            length = user32.GetWindowTextLengthW(handle)
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(handle, buffer, len(buffer))
            process_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(handle, ctypes.byref(process_id))
            process_name = psutil.Process(process_id.value).name().removesuffix(".exe").lower()
            if process_name in {"python", "pythonw"} and "i.s.a.b.e.l.l.a" in buffer.value.lower():
                process_name = "isabella"
            return ActiveWindow(process_name or "unavailable", buffer.value or "unavailable", True)
        except (AttributeError, OSError, psutil.Error):
            return ActiveWindow("unavailable", "unavailable", False)

    def connected_devices(self) -> dict[str, str]:
        devices: dict[str, str] = {}
        try:
            import sounddevice as sd

            default_input, default_output = sd.default.device
            if default_input is not None and int(default_input) >= 0:
                devices["microphone"] = str(sd.query_devices(default_input)["name"])
            if default_output is not None and int(default_output) >= 0:
                devices["audio_output"] = str(sd.query_devices(default_output)["name"])
        except Exception:
            return devices
        return devices
