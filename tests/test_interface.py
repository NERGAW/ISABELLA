import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from Isabella.Interface.controller import InterfaceController
from Isabella.Interface.hud import IsabellaHUD
from Isabella.Interface.models import MessageRole, UIMessage, UIState
from Isabella.Interface.workers import BrainWorker
from Isabella.Intelligence.models import BrainResponse, Intent


QT_APP = QApplication.instance() or QApplication([])


class FakeRouter:
    def route(self, text):
        return Intent.CONVERSATION


class FakeBrain:
    router = FakeRouter()
    registry = {"test.echo": object()}

    class LLM:
        @staticmethod
        def health_check():
            return True

    llm = LLM()

    def process(self, text, intent=None):
        return BrainResponse(Intent.CONVERSATION, f"Resposta: {text}")


class FakeListener:
    def __init__(self):
        self.enabled = True

    def set_microphone_enabled(self, enabled):
        self.enabled = enabled


class FakeTTS:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


class FakeApp:
    def __init__(self):
        self.voice_listener = FakeListener()
        self.tts_manager = FakeTTS()
        self.spoken = []
        self.closed = False

    def start_voice(self, callback):
        self.voice_callback = callback
        return True

    def start_tts(self, state_callback=None):
        self.tts_callback = state_callback
        return True

    def speak(self, text):
        self.spoken.append(text)

    def shutdown(self):
        self.closed = True


def flush(controller):
    assert controller.thread_pool.waitForDone(2000)
    QT_APP.processEvents()


def test_ui_models_expose_all_required_states():
    assert {state.value for state in UIState} == {
        "IDLE", "LISTENING", "TRANSCRIBING", "THINKING", "PLANNING",
        "EXECUTING", "SPEAKING", "ERROR",
    }
    assert UIMessage(MessageRole.USER, "oi").text == "oi"


def test_controller_processes_message_and_speaks_without_blocking_gui():
    app = FakeApp()
    controller = InterfaceController(app, FakeBrain())
    controller.submit_text("olá")
    flush(controller)
    assert [message.text for message in controller.messages] == ["olá", "Resposta: olá"]
    assert app.spoken == ["Resposta: olá"]
    assert controller.busy is False
    assert controller.state is UIState.IDLE


def test_thirty_alternating_interactions_preserve_order():
    controller = InterfaceController(FakeApp(), FakeBrain())
    for index in range(30):
        controller.submit_text(f"pedido {index}")
        flush(controller)
    texts = [message.text for message in controller.messages]
    assert len(texts) == 60
    for index in range(30):
        assert texts[index * 2:index * 2 + 2] == [
            f"pedido {index}", f"Resposta: pedido {index}",
        ]


def test_controller_caps_history_and_controls_audio():
    app = FakeApp()
    controller = InterfaceController(app, FakeBrain(), message_limit=3)
    for index in range(5):
        controller.add_message(MessageRole.USER, str(index))
    controller.toggle_microphone(False)
    controller.stop_speech()
    assert [message.text for message in controller.messages] == ["2", "3", "4"]
    assert app.voice_listener.enabled is False
    assert app.tts_manager.stopped is True


def test_worker_reports_backend_failure():
    brain = FakeBrain()
    brain.process = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("falha simulada"))
    worker = BrainWorker(brain, "teste")
    errors = []
    worker.signals.error.connect(errors.append)
    worker.run()
    assert errors == ["falha simulada"]


def test_hud_inserts_and_limits_visible_messages():
    controller = InterfaceController(FakeApp(), FakeBrain())
    window = IsabellaHUD(controller)
    for index in range(105):
        controller.add_message(MessageRole.USER, f"mensagem {index}")
    QT_APP.processEvents()
    assert window.history.count() == 100
    assert window.minimumWidth() <= 820
    window.close()


def test_backend_start_contract_does_not_need_to_return_itself(monkeypatch):
    """The production start method initializes in place and returns None."""
    from Isabella.Interface import hud

    class StartingApp(FakeApp):
        def start(self):
            return None

    backend = StartingApp()
    monkeypatch.setattr(hud, "IsabellaApp", lambda: backend)
    monkeypatch.setattr(hud.Brain, "from_config", lambda: FakeBrain())
    monkeypatch.setattr(QT_APP, "exec", lambda: 0)
    assert hud.run_gui() == 0
    assert hasattr(backend, "voice_callback")
