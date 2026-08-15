from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from Isabella.Context.manager import ContextManager
from Isabella.Intelligence.brain import Brain
from Isabella.Intelligence.models import Intent
from Isabella.Skills import build_default_registry
from Isabella.Skills.system import create_system_skills
from Isabella.Skills.vision import create_vision_skills
from Isabella.Vision.camera import CameraCapture
from Isabella.Vision.manager import VisionManager, load_vision_config
from Isabella.Vision.models import ImageCapture, VisionResult, VisionSource, now_iso
from Isabella.Vision.screen import ScreenCapturer


VISION_CONFIG = {
    "enabled": True,
    "screen_capture_enabled": True,
    "camera_enabled": True,
    "camera_device": None,
    "continuous_capture": False,
    "max_image_width": 1920,
    "max_image_height": 1080,
    "temporary_images": True,
    "multimodal_model": None,
}

CONTEXT_CONFIG = {
    "enabled": True, "active_window_lookup": True,
    "refresh_interval_seconds": 1.0, "reference_confidence_threshold": 0.8,
}


class FakeScreen:
    def __init__(self, folder, fail=False):
        self.folder = folder
        self.fail = fail
        self.latencies_ms = []
        self.index = 0

    def _capture(self, source):
        if self.fail:
            raise RuntimeError("display unavailable")
        self.index += 1
        path = self.folder / f"screen-{self.index}.png"
        path.write_bytes(b"png")
        self.latencies_ms.append(1.0)
        return ImageCapture(source, now_iso(), 800, 600, path=path, active_window="Editor", temporary=True)

    def capture_screen(self, screen_index=0, temporary=True):
        return self._capture(VisionSource.SCREEN)

    def capture_active_window(self, temporary=True):
        return self._capture(VisionSource.ACTIVE_WINDOW)

    def health_check(self):
        return not self.fail


class FakeCamera:
    def __init__(self, folder, fail=False):
        self.folder = folder
        self.fail = fail
        self.closed = 0
        self.latencies_ms = []

    def capture_frame(self, temporary=True):
        if self.fail:
            raise RuntimeError("camera unavailable")
        path = self.folder / "camera.jpg"
        path.write_bytes(b"jpg")
        self.latencies_ms.append(2.0)
        return ImageCapture(VisionSource.CAMERA, now_iso(), 640, 480, path=path, temporary=True)

    def close(self):
        self.closed += 1

    def health_check(self):
        return not self.fail


class FakeWindowProvider:
    class Window:
        application = "code"
        title = "Editor"

    def active_window(self):
        return self.Window()


class FakeVideo:
    def __init__(self, opened=True):
        self.opened = opened
        self.released = False

    def isOpened(self):
        return self.opened

    def read(self):
        return self.opened, np.zeros((480, 640, 3), dtype=np.uint8) if self.opened else None

    def release(self):
        self.released = True


class FakeCV2:
    def __init__(self, opened=True):
        self.opened = opened
        self.instances = []

    def VideoCapture(self, device):
        instance = FakeVideo(self.opened and device == 0)
        self.instances.append(instance)
        return instance

    @staticmethod
    def resize(frame, size):
        return np.zeros((size[1], size[0], 3), dtype=np.uint8)

    @staticmethod
    def imwrite(path, frame):
        Path(path).write_bytes(b"jpg")
        return True


class FakeLLM:
    def chat(self, text):
        return "texto"

    def close(self):
        pass


def manager(tmp_path, screen=None, camera=None, context=None):
    return VisionManager(
        VISION_CONFIG, screen=screen or FakeScreen(tmp_path),
        camera=camera or FakeCamera(tmp_path), context=context,
    )


def test_vision_models_have_required_capture_metadata(tmp_path):
    capture = ImageCapture(VisionSource.SCREEN, now_iso(), 100, 50, path=tmp_path / "a.png")
    assert capture.source is VisionSource.SCREEN
    assert capture.width == 100
    assert VisionResult(True, "ok", capture).success


def test_config_forbids_continuous_capture(tmp_path):
    path = tmp_path / "vision.json"
    import json

    path.write_text(json.dumps(dict(VISION_CONFIG, continuous_capture=True)), encoding="utf-8")
    with pytest.raises(Exception, match="Continuous"):
        load_vision_config(path)


def test_screen_capturer_resizes_and_writes_png(tmp_path):
    capturer = ScreenCapturer(320, 200, grabber=lambda bbox=None: Image.new("RGB", (1280, 720)))
    capturer.monitor_bounds = lambda: [(0, 0, 1280, 720)]
    capture = capturer.capture_screen(destination=tmp_path / "capture.png", temporary=False)
    assert capture.path.is_file()
    assert capture.width <= 320 and capture.height <= 200
    assert capture.temporary is False


def test_active_window_capture_uses_window_metadata(tmp_path, monkeypatch):
    capturer = ScreenCapturer(800, 600, grabber=lambda bbox=None: Image.new("RGB", (400, 300)), window_provider=FakeWindowProvider())
    monkeypatch.setattr(capturer, "active_window_bounds", lambda: (1, 2, 401, 302))
    capture = capturer.capture_active_window(destination=tmp_path / "window.png")
    assert capture.active_window == "Editor"
    assert capture.metadata["application"] == "code"


def test_camera_lists_opens_captures_and_closes(tmp_path):
    backend = FakeCV2()
    camera = CameraCapture(0, backend=backend)
    assert camera.list_cameras(3) == [0]
    capture = camera.capture_frame(tmp_path / "camera.jpg", temporary=False)
    assert capture.width == 640 and capture.height == 480
    camera.close()
    assert backend.instances[-1].released


def test_camera_unavailable_health_and_capture(tmp_path):
    camera = CameraCapture(0, backend=FakeCV2(opened=False))
    assert camera.health_check() is False
    with pytest.raises(Exception, match="unavailable"):
        camera.capture_frame(tmp_path / "missing.jpg")


def test_manager_captures_all_sources_and_updates_light_context(tmp_path):
    context = ContextManager(CONTEXT_CONFIG)
    vision = manager(tmp_path, context=context)
    assert vision.capture_screen().success
    assert vision.capture_active_window().success
    assert vision.capture_camera().success
    snapshot = context.get_snapshot()
    assert snapshot.last_vision_source == "CAMERA"
    assert snapshot.last_capture_timestamp
    assert not hasattr(snapshot, "image")
    vision.shutdown()


def test_capture_failures_return_results_without_raising(tmp_path):
    vision = manager(tmp_path, FakeScreen(tmp_path, fail=True), FakeCamera(tmp_path, fail=True))
    assert vision.capture_screen().error_code == "SCREEN_CAPTURE_FAILED"
    assert vision.capture_active_window().error_code == "ACTIVE_WINDOW_CAPTURE_FAILED"
    assert vision.capture_camera().error_code == "CAMERA_UNAVAILABLE"
    assert vision.status == "ONLINE"


def test_temporary_files_are_bounded_cleaned_and_shutdown_closes_camera(tmp_path):
    camera = FakeCamera(tmp_path)
    vision = manager(tmp_path, camera=camera)
    captures = [vision.capture_screen().capture for _ in range(7)]
    assert not captures[0].path.exists()
    assert sum(capture.path.exists() for capture in captures) == 5
    vision.cleanup(captures[-1])
    assert not captures[-1].path.exists()
    vision.shutdown()
    assert not any(capture.path.exists() for capture in captures)
    assert camera.closed >= 1


def test_vision_skills_are_safe_and_return_capture_data(tmp_path):
    vision = manager(tmp_path)
    definitions = create_vision_skills(vision)
    assert all(definition.risk_level.value == "SAFE" for definition in definitions)
    result = definitions[0].executor({})
    assert result.success
    assert result.data["source"] == "SCREEN"
    vision.shutdown()


def test_legacy_system_screenshot_reuses_common_capturer(tmp_path, monkeypatch):
    import Isabella.Skills.system as system_module

    monkeypatch.setattr(system_module, "SCREENSHOT_DIRECTORY", tmp_path)
    class CommonCapturer:
        called = False

        def capture_screen(self, screen_index=0, destination=None, temporary=True):
            self.called = True
            destination.write_bytes(b"png")
            return ImageCapture(VisionSource.SCREEN, now_iso(), 10, 10, path=destination, temporary=temporary)

    common = CommonCapturer()
    skill = next(item for item in create_system_skills(screen_capturer=common) if item.id == "system.screenshot")
    result = skill.executor({})
    assert result.success and common.called


def test_brain_screen_question_does_not_fake_multimodal_analysis(tmp_path):
    vision = manager(tmp_path)
    brain = Brain(FakeLLM(), registry=build_default_registry(vision), vision=vision)
    response = brain.process("O que está aparecendo na minha tela?")
    assert response.response_type is Intent.CONVERSATION
    assert "não possui capacidade multimodal" in response.message
    assert vision.screen.index == 1
    brain.shutdown()


@pytest.mark.parametrize(
    ("text", "skill"),
    [
        ("Capture minha tela", "vision.capture_screen"),
        ("Capture a janela ativa", "vision.capture_active_window"),
        ("Capture uma imagem da câmera", "vision.capture_camera"),
    ],
)
def test_router_and_registry_vision_integration(tmp_path, text, skill):
    vision = manager(tmp_path)
    brain = Brain(FakeLLM(), registry=build_default_registry(vision), vision=vision)
    response = brain.process(text)
    assert response.skill_request.skill == skill
    assert response.skill_results[0].success
    brain.shutdown()
