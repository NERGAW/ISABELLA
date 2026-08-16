"""Signal-based bridge between the HUD and backend services."""

import logging
from PySide6.QtCore import QObject, QThreadPool, Signal, Slot

from Isabella.Events import EventType
from .models import MessageRole, MessageType, SUBSYSTEMS, UIMessage, UIState
from .workers import BrainWorker, FunctionWorker


LOGGER = logging.getLogger("UI")


class InterfaceController(QObject):
    message_added = Signal(object)
    state_changed = Signal(str)
    subsystem_changed = Signal(str, str)
    busy_changed = Signal(bool)
    confirmation_required = Signal(object)
    backend_latency = Signal(float)
    voice_command_received = Signal(str)
    tts_speaking_received = Signal(bool)
    context_changed = Signal(object)
    diagnostics_received = Signal(object)
    control_center_requested = Signal()

    def __init__(self, app, brain, message_limit: int = 100) -> None:
        super().__init__()
        self.app = app
        self.brain = brain
        self.message_limit = message_limit
        self.messages: list[UIMessage] = []
        self.state = UIState.IDLE
        self.subsystems = {name: "OFFLINE" for name in SUBSYSTEMS}
        self.busy = False
        self.microphone_enabled = True
        self.thread_pool = QThreadPool(self)
        self.thread_pool.setMaxThreadCount(1)
        self._workers: set[object] = set()
        self._request_sequence = 0
        self._active_correlation_id: str | None = None
        self._pending_confirmation = None
        self.event_bus = getattr(app, "event_bus", None)
        self.managed_by_runtime = False
        diagnostics = getattr(brain, "diagnostics", None)
        if diagnostics:
            diagnostics.bind(app=app, brain=brain, controller=self, event_bus=self.event_bus)
        self.voice_command_received.connect(self.submit_voice_text)
        self.tts_speaking_received.connect(self.set_tts_speaking)
        if self.event_bus:
            self.event_bus.subscribe("tts.*", self._on_tts_event)
            self.event_bus.subscribe("vision.*", self._on_vision_event)
            self.event_bus.subscribe("research.*", self._on_research_event)

    def _on_tts_event(self, event) -> None:
        if event.type == EventType.TTS_STARTED.value:
            self.tts_speaking_received.emit(True)
        elif event.type in {
            EventType.TTS_COMPLETED.value, EventType.TTS_STOPPED.value, EventType.TTS_FAILED.value,
        }:
            self.tts_speaking_received.emit(False)
        status = "ERROR" if event.type == EventType.TTS_FAILED.value else "ONLINE"
        self.subsystem_changed.emit("VOICE OUTPUT", status)

    def _on_vision_event(self, event) -> None:
        status = "ERROR" if event.type == EventType.VISION_CAPTURE_FAILED.value else "ONLINE"
        self.subsystem_changed.emit("VISION", status)

    def _on_research_event(self, event) -> None:
        status = "ERROR" if event.type == EventType.RESEARCH_FAILED.value else "ONLINE"
        self.subsystem_changed.emit("RESEARCH", status)

    def start_services(self, start_backends: bool = True, run_health_check: bool = True) -> None:
        self.update_subsystem("CORE", "ONLINE")
        self.update_subsystem("SKILLS", "ONLINE" if self.brain.registry else "DEGRADED")
        self.update_subsystem("PLANNER", "ONLINE")
        memory = getattr(self.brain, "memory", None)
        self.update_subsystem("MEMORY", getattr(memory, "status", "OFFLINE"))
        context = getattr(self.brain, "context", None)
        self.update_subsystem("CONTEXT", getattr(context, "status", "OFFLINE"))
        vision = getattr(self.brain, "vision", None)
        self.update_subsystem("VISION", getattr(vision, "status", "OFFLINE"))
        research = getattr(self.brain, "research", None)
        research_health = research.health_check() if research else {}
        self.update_subsystem("RESEARCH", "ONLINE" if research_health.get("provider_configured") else "DEGRADED")
        if context:
            context.refresh_active_window(force=True)
            context.refresh_devices()
        voice_ok = self.app.start_voice(self.voice_command_received.emit) if start_backends else bool(self.app.voice_listener)
        self.update_subsystem("VOICE INPUT", "ONLINE" if voice_ok else "DEGRADED")
        if context:
            context.set("voice_state", UIState.LISTENING.value if voice_ok else UIState.ERROR.value)
            context.set_system_state("HUD", "ONLINE")
        tts_ok = self.app.start_tts(state_callback=self.tts_speaking_received.emit) if start_backends else bool(self.app.tts_manager)
        self.update_subsystem("VOICE OUTPUT", "ONLINE" if tts_ok else "DEGRADED")
        if run_health_check:
            self._start_health_check()
        self.add_message(MessageRole.SYSTEM, "Interface pronta.", MessageType.STATUS)
        LOGGER.info("started")
        if context:
            self.context_changed.emit(context.get_snapshot())

    def _start_health_check(self) -> None:
        worker = FunctionWorker(self.brain.llm.health_check)
        worker.signals.result.connect(lambda healthy, latency: self.update_subsystem("LLM", "ONLINE" if healthy else "DEGRADED"))
        worker.signals.error.connect(lambda error: self.update_subsystem("LLM", "ERROR"))
        self._start_worker(worker)

    @Slot(str)
    def submit_text(self, text: str) -> None:
        self._submit(text, from_voice=False)

    @Slot(str)
    def submit_voice_text(self, text: str) -> None:
        self._submit(text, from_voice=True)

    def _submit(self, text: str, from_voice: bool) -> None:
        cleaned = text.strip()
        if not cleaned:
            return
        normalized = cleaned.casefold().strip(" .!?")
        if normalized in {"isabella, abra o control center", "isabella abra o control center", "abra o control center", "control center"}:
            self.add_message(MessageRole.USER, cleaned)
            self.control_center_requested.emit()
            self.add_message(MessageRole.ISABELLA, "Abrindo o Control Center.", MessageType.ACTION)
            return
        if from_voice and self._pending_confirmation:
            if normalized in {"sim", "confirmo", "pode confirmar", "pode executar"}:
                request = self._pending_confirmation
                self._pending_confirmation = None
                self.confirm_critical(request, source="voice")
                return
            if normalized in {"não", "nao", "cancelar", "cancele"}:
                self.cancel_critical()
                return
        if self.busy:
            self.add_message(MessageRole.SYSTEM, "Aguarde a solicitação atual terminar.", MessageType.STATUS)
            return
        self.add_message(MessageRole.USER, cleaned)
        LOGGER.info("user message source=%s", "voice" if from_voice else "text")
        self._set_busy(True)
        self.set_state(UIState.THINKING)
        self._request_sequence += 1
        request_id = f"ui-{self._request_sequence:06d}"
        self._active_correlation_id = request_id
        if self.event_bus and from_voice:
            self.event_bus.emit(
                EventType.VOICE_COMMAND, "ui", {"command": cleaned}, correlation_id=request_id,
            )
        worker = BrainWorker(
            self.brain, cleaned, request_id=request_id,
            input_source="voice" if from_voice else "text",
        )
        worker.signals.phase.connect(lambda phase: self.set_state(UIState(phase)))
        worker.signals.result.connect(self._handle_brain_response)
        worker.signals.error.connect(self._handle_error)
        worker.signals.finished.connect(lambda: self._set_busy(False))
        self._start_worker(worker)
        LOGGER.info("backend request")

    @Slot(object, float)
    def _handle_brain_response(self, response, latency_ms: float) -> None:
        self.backend_latency.emit(latency_ms)
        self.add_message(MessageRole.ISABELLA, response.message, MessageType.ACTION)
        LOGGER.info("response received latency_ms=%.2f", latency_ms)
        pending = next((result for result in response.skill_results if result.status == "confirmation_required"), None)
        diagnostics = next((result for result in response.skill_results if result.skill_id == "system.diagnostics" and result.success), None)
        if diagnostics and diagnostics.data.get("detailed"):
            self.diagnostics_received.emit(diagnostics.data["report"])
        if pending:
            request = self.brain.pending_confirmation(pending.data["confirmation_id"])
            if request:
                self._pending_confirmation = request
                self._set_busy(False)
                self.confirmation_required.emit(request)
        else:
            try:
                self.app.speak(response.message, correlation_id=self._active_correlation_id)
            except TypeError:
                self.app.speak(response.message)
        self.set_state(UIState.IDLE)
        context = getattr(self.brain, "context", None)
        if context:
            self.context_changed.emit(context.get_snapshot())

    def confirm_critical(self, request, source: str = "hud") -> None:
        if self.busy:
            return
        self._set_busy(True)
        self.set_state(UIState.EXECUTING)
        self._pending_confirmation = None
        worker = FunctionWorker(self.brain.confirm, request, source)
        worker.signals.result.connect(self._handle_confirmation_result)
        worker.signals.error.connect(self._handle_error)
        worker.signals.finished.connect(lambda: self._set_busy(False))
        self._start_worker(worker)

    @Slot(object, float)
    def _handle_confirmation_result(self, result, latency_ms: float) -> None:
        self.add_message(MessageRole.ISABELLA, result.message, MessageType.ACTION)
        self.app.speak(result.message)
        self.backend_latency.emit(latency_ms)
        self.set_state(UIState.IDLE)

    def cancel_critical(self) -> None:
        if self._pending_confirmation:
            self.brain.cancel_confirmation(self._pending_confirmation.id)
            self._pending_confirmation = None
        self.add_message(MessageRole.SYSTEM, "Ação crítica cancelada.", MessageType.STATUS)
        self.app.speak("Ação cancelada.")
        self.set_state(UIState.IDLE)

    @Slot(str)
    def _handle_error(self, error: str) -> None:
        self.add_message(MessageRole.ERROR, f"Falha no backend: {error}", MessageType.ERROR)
        self.set_state(UIState.ERROR)
        LOGGER.error("error=%s", error)

    def _start_worker(self, worker) -> None:
        self._workers.add(worker)
        worker.signals.finished.connect(lambda current=worker: self._workers.discard(current))
        self.thread_pool.start(worker)

    def add_message(self, role: MessageRole, text: str, message_type: MessageType = MessageType.TEXT) -> None:
        message = UIMessage(role, text, type=message_type)
        self.messages.append(message)
        if len(self.messages) > self.message_limit:
            del self.messages[: len(self.messages) - self.message_limit]
        self.message_added.emit(message)
        if self.event_bus:
            self.event_bus.emit(
                EventType.UI_MESSAGE, "ui",
                {"role": role.value, "type": message_type.value, "text": text},
                correlation_id=self._active_correlation_id,
            )

    def set_state(self, state: UIState) -> None:
        self.state = state
        self.state_changed.emit(state.value)
        LOGGER.info("state changed=%s", state.value)
        context = getattr(self.brain, "context", None)
        if context:
            context.set("voice_state", state.value)

    @Slot(bool)
    def set_tts_speaking(self, speaking: bool) -> None:
        if speaking:
            self.set_state(UIState.SPEAKING)
        elif not self.busy:
            self.set_state(UIState.IDLE)

    def update_subsystem(self, name: str, status: str) -> None:
        self.subsystems[name] = status
        self.subsystem_changed.emit(name, status)
        context = getattr(self.brain, "context", None)
        if context and name != "CONTEXT":
            context.set_system_state(name, status)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.busy_changed.emit(busy)

    def toggle_microphone(self, enabled: bool) -> None:
        self.microphone_enabled = enabled
        if self.app.voice_listener:
            self.app.voice_listener.set_microphone_enabled(enabled)
        self.update_subsystem("VOICE INPUT", "ONLINE" if enabled else "OFFLINE")
        self.set_state(UIState.LISTENING if enabled else UIState.IDLE)

    set_microphone_enabled = toggle_microphone

    def stop_speech(self) -> None:
        if self.app.tts_manager:
            self.app.tts_manager.stop()
        self.set_state(UIState.IDLE)

    def shutdown(self) -> None:
        if self.event_bus:
            self.event_bus.unsubscribe("tts.*", self._on_tts_event)
            self.event_bus.unsubscribe("vision.*", self._on_vision_event)
            self.event_bus.unsubscribe("research.*", self._on_research_event)
        self.thread_pool.clear()
        self.thread_pool.waitForDone(5000)
        if not self.managed_by_runtime:
            shutdown = getattr(self.brain, "shutdown", None)
            if shutdown:
                shutdown()
            self.app.shutdown()
