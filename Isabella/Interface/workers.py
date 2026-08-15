"""Qt workers that keep backend operations away from the GUI thread."""

from time import perf_counter
from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from Isabella.Intelligence.models import Intent


class WorkerSignals(QObject):
    phase = Signal(str)
    result = Signal(object, float)
    error = Signal(str)
    finished = Signal()


class BrainWorker(QRunnable):
    def __init__(self, brain, text: str, request_id: str = "ui", input_source: str = "text") -> None:
        super().__init__()
        self.brain = brain
        self.text = text
        self.request_id = request_id
        self.input_source = input_source
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        started = perf_counter()
        try:
            router_started = perf_counter()
            intent = self.brain.router.route(self.text)
            router_ms = (perf_counter() - router_started) * 1000
            phase = {
                Intent.CONVERSATION: "THINKING",
                Intent.SINGLE_SKILL: "EXECUTING",
                Intent.MULTI_STEP: "PLANNING",
            }[intent]
            self.signals.phase.emit(phase)
            try:
                response = self.brain.process(
                    self.text, intent=intent, request_id=self.request_id,
                    input_source=self.input_source, router_ms=router_ms,
                )
            except TypeError as exc:
                if "unexpected keyword argument" not in str(exc):
                    raise
                response = self.brain.process(self.text, intent=intent)
            self.signals.result.emit(response, (perf_counter() - started) * 1000)
        except Exception as exc:
            self.signals.error.emit(str(exc) or exc.__class__.__name__)
        finally:
            self.signals.finished.emit()


class FunctionWorker(QRunnable):
    def __init__(self, function, *args) -> None:
        super().__init__()
        self.function = function
        self.args = args
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        started = perf_counter()
        try:
            value = self.function(*self.args)
            self.signals.result.emit(value, (perf_counter() - started) * 1000)
        except Exception as exc:
            self.signals.error.emit(str(exc) or exc.__class__.__name__)
        finally:
            self.signals.finished.emit()
