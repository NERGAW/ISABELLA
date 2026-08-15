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
from Isabella.Vision.models import ScreenAnalysis
from Isabella.Vision.provider import OllamaVisionProvider, VisionProviderError
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
    "multimodal_enabled": True,
    "vision_provider": "ollama",
    "vision_model": "qwen3-vl:2b",
    "max_image_size": 1280,
    "compression_quality": 85,
    "provider_local": True,
    "allow_cloud_upload": False,
    "analysis_context_ttl_seconds": 120,
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


class FakeVisionProvider:
    local = True

    def __init__(self, analysis=None, unavailable=False):
        self.analysis = analysis or ScreenAnalysis("Uma janela simples está visível.", confidence=0.9)
        self.unavailable = unavailable
        self.calls = 0
        self.closed = False

    def preprocess(self, capture):
        return "base64-image", {"width": 800, "height": 600, "bytes": 100, "format": "JPEG"}

    def analyze(self, image, question):
        self.calls += 1
        if self.unavailable:
            raise VisionProviderError("offline")
        return self.analysis

    def health_check(self):
        return {"reachable": not self.unavailable, "model_available": not self.unavailable, "model": "fake", "local": True}

    def close(self):
        self.closed = True


def manager(tmp_path, screen=None, camera=None, context=None, provider=None):
    return VisionManager(
        VISION_CONFIG, screen=screen or FakeScreen(tmp_path),
        camera=camera or FakeCamera(tmp_path), context=context,
        provider=provider or FakeVisionProvider(),
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


def test_config_requires_explicit_cloud_permission(tmp_path):
    path = tmp_path / "vision.json"
    import json

    path.write_text(json.dumps(dict(VISION_CONFIG, provider_local=False, allow_cloud_upload=False)), encoding="utf-8")
    with pytest.raises(Exception, match="explicit"):
        load_vision_config(path)


def test_screen_capturer_resizes_and_writes_png(tmp_path):
    capturer = ScreenCapturer(320, 200, grabber=lambda bbox=None: Image.new("RGB", (1280, 720)))
    capturer.monitor_bounds = lambda: [(0, 0, 1280, 720)]
    capture = capturer.capture_screen(destination=tmp_path / "capture.png", temporary=False)
    assert capture.path.is_file()
    assert capture.width <= 320 and capture.height <= 200
    assert capture.temporary is False


def test_multimodal_preprocess_resizes_and_compresses_before_inference(tmp_path):
    path = tmp_path / "large.png"
    Image.new("RGB", (3840, 2160), "white").save(path)
    capture = ImageCapture(VisionSource.SCREEN, now_iso(), 3840, 2160, path=path, temporary=False)
    provider = OllamaVisionProvider("vision", "http://localhost:11434", 1, 1280, 80)
    encoded, metadata = provider.preprocess(capture)
    assert encoded
    assert metadata["width"] == 1280 and metadata["height"] == 720
    assert metadata["bytes"] < 1280 * 720 * 3
    provider.close()


def test_provider_accepts_qwen_thinking_envelope_and_normalizes_optional_types():
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "", "thinking": '{"summary":"Tela simples","visible_text":"Linha A\\nLinha B","confidence":"0.75"}'}}

    class Session:
        def post(self, *args, **kwargs):
            return Response()

        def close(self):
            pass

    provider = OllamaVisionProvider("vision", "http://local", 1, 1280, 80, session=Session())
    analysis = provider.analyze("image", "question")
    assert analysis.visible_text == ("Linha A", "Linha B")
    assert analysis.confidence == 0.75


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


def test_brain_screen_question_uses_separate_multimodal_analysis(tmp_path):
    provider = FakeVisionProvider(ScreenAnalysis("O desktop mostra apenas uma janela de editor.", applications=("VS Code",), confidence=0.95))
    vision = manager(tmp_path, provider=provider)
    brain = Brain(FakeLLM(), registry=build_default_registry(vision), vision=vision)
    response = brain.process("O que está aparecendo na minha tela?")
    assert response.response_type is Intent.VISION
    assert "desktop" in response.message
    assert vision.screen.index == 1
    assert provider.calls == 1
    brain.shutdown()


@pytest.mark.parametrize(
    ("label", "analysis"),
    [
        ("desktop vazio", ScreenAnalysis("Desktop vazio, sem janelas abertas.", confidence=0.98)),
        ("browser", ScreenAnalysis("Um navegador mostra uma página.", applications=("Browser",), ui_elements=("barra de endereço",), confidence=0.9)),
        ("VS Code", ScreenAnalysis("O VS Code mostra um arquivo Python.", applications=("VS Code",), confidence=0.9)),
        ("erro Python", ScreenAnalysis("Um traceback Python está visível.", errors=("NameError na linha exibida",), confidence=0.94)),
        ("terminal", ScreenAnalysis("Um terminal está aberto.", applications=("Terminal",), confidence=0.88)),
        ("janela múltipla", ScreenAnalysis("Duas janelas estão lado a lado.", applications=("Browser", "Editor"), confidence=0.8)),
        ("texto pequeno", ScreenAnalysis("Há texto pequeno que não está totalmente legível.", confidence=0.4)),
    ],
)
def test_structured_screen_scenarios_are_preserved_without_forcing_fields(tmp_path, label, analysis):
    vision = manager(tmp_path, provider=FakeVisionProvider(analysis))
    result = vision.analyze_screen(f"Analise: {label}")
    assert result.success
    assert result.analysis.summary == analysis.summary
    assert set(result.analysis.to_dict()) <= {"summary", "visible_text", "applications", "errors", "ui_elements", "confidence"}
    assert set(result.timings_ms) == {"capture_ms", "preprocess_ms", "vision_inference_ms", "total_ms"}
    vision.shutdown()


def test_model_unavailable_fails_cleanly_and_removes_capture(tmp_path):
    provider = FakeVisionProvider(unavailable=True)
    vision = manager(tmp_path, provider=provider)
    result = vision.analyze_screen("O que aparece?")
    assert not result.success and result.error_code == "VISION_INFERENCE_FAILED"
    assert not (tmp_path / "screen-1.png").exists()
    assert vision.status == "ONLINE"
    vision.shutdown()


def test_follow_up_reuses_recent_structured_analysis_without_new_screenshot(tmp_path):
    analysis = ScreenAnalysis("Um traceback está visível.", errors=("TypeError: valor inválido",), confidence=0.9)
    provider = FakeVisionProvider(analysis)
    vision = manager(tmp_path, provider=provider)
    brain = Brain(FakeLLM(), vision=vision)
    first = brain.process("Isabella, olhe minha tela.")
    second = brain.process("O que significa esse erro?")
    assert first.response_type is Intent.VISION and second.response_type is Intent.VISION
    assert "TypeError" in second.message
    assert vision.screen.index == 1 and provider.calls == 1
    brain.shutdown()


def test_analysis_updates_only_lightweight_context_fields(tmp_path):
    context = ContextManager(CONTEXT_CONFIG)
    analysis = ScreenAnalysis(
        "Editor com traceback.", applications=("VS Code",),
        errors=("ValueError: entrada inválida",), confidence=0.9,
    )
    vision = manager(tmp_path, context=context, provider=FakeVisionProvider(analysis))
    assert vision.analyze_screen("Analise o erro").success
    snapshot = context.get_snapshot()
    assert snapshot.last_screen_summary == "Editor com traceback."
    assert snapshot.last_detected_error == "ValueError: entrada inválida"
    assert snapshot.last_visible_application == "VS Code"
    assert not hasattr(snapshot, "screenshot") and not hasattr(snapshot, "image_buffer")
    vision.shutdown()


def test_simple_screen_hallucination_guard_reports_uncertainty_and_no_objects(tmp_path):
    analysis = ScreenAnalysis("Uma área branca simples; não há detalhes suficientes.", confidence=0.25)
    result = manager(tmp_path, provider=FakeVisionProvider(analysis)).analyze_screen("Descreva somente o visível")
    assert result.analysis.applications == ()
    assert result.analysis.visible_text == ()
    assert "baixa confiança" in result.message


@pytest.mark.parametrize(
    "text",
    ["O que está aparecendo na minha tela?", "Que erro é esse?", "O que significa essa mensagem?", "Resuma o que está aberto."],
)
def test_router_recognizes_multimodal_vision_intent(text):
    from Isabella.Intelligence.router import Router

    assert Router().route(text) is Intent.VISION


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
