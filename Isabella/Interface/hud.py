"""Lightweight responsive desktop HUD for I.S.A.B.E.L.L.A."""

from __future__ import annotations

import html
import sys
import logging
from time import perf_counter

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QKeyEvent
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QSplitter,
    QVBoxLayout, QWidget,
)

from Isabella.Core.app import IsabellaApp
from Isabella.Intelligence.brain import Brain
from .controller import InterfaceController
from .models import MessageRole, SUBSYSTEMS, UIMessage


STYLE = """
QMainWindow, QWidget { background:#0d1117; color:#e6edf3; font:10pt "Segoe UI"; }
QFrame#panel { background:#161b22; border:1px solid #30363d; border-radius:12px; }
QLabel#title { font-size:19pt; font-weight:650; color:#f0f6fc; }
QLabel#muted { color:#8b949e; }
QLabel[kind="ok"] { color:#3fb950; } QLabel[kind="busy"] { color:#d29922; }
QLabel[kind="error"] { color:#f85149; }
QLabel#user { background:#1f6feb; color:white; border-radius:10px; padding:10px; }
QLabel#assistant { background:#21262d; border:1px solid #30363d; border-radius:10px; padding:10px; }
QLabel#system { color:#8b949e; padding:6px; }
QLabel#error { background:#3d1518; color:#ff7b72; border-radius:10px; padding:10px; }
QListWidget { background:transparent; border:none; outline:none; }
QListWidget::item { border:none; padding:3px; }
QPlainTextEdit { background:#0d1117; border:1px solid #30363d; border-radius:9px; padding:8px; }
QPlainTextEdit:focus { border-color:#58a6ff; }
QPushButton { background:#21262d; border:1px solid #30363d; border-radius:8px; padding:8px 13px; font-weight:600; }
QPushButton:hover { background:#30363d; } QPushButton:checked { background:#1f6feb; border-color:#58a6ff; }
QPushButton#send { background:#238636; border-color:#2ea043; }
QPushButton:disabled, QPlainTextEdit:disabled { color:#6e7681; background:#161b22; }
QSplitter::handle { background:transparent; width:8px; }
"""


class CommandInput(QPlainTextEdit):
    submitted = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.submitted.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class IsabellaHUD(QMainWindow):
    def __init__(self, controller: InterfaceController) -> None:
        super().__init__()
        self.controller = controller
        self._statuses: dict[str, QLabel] = {}
        self._build()
        self._connect()

    def _build(self) -> None:
        self.setWindowTitle("I.S.A.B.E.L.L.A.")
        self.setMinimumSize(820, 560)
        self.resize(1120, 720)
        self.setStyleSheet(STYLE)
        root, layout = QWidget(), QVBoxLayout()
        root.setLayout(layout)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        top = QFrame(objectName="panel")
        top_row = QHBoxLayout(top)
        identity = QVBoxLayout()
        identity.addWidget(QLabel("I.S.A.B.E.L.L.A.", objectName="title"))
        identity.addWidget(QLabel("Assistente local • pronta para colaborar", objectName="muted"))
        top_row.addLayout(identity, 1)
        self.state_label = QLabel("IDLE")
        self._repolish(self.state_label, "ok")
        top_row.addWidget(self.state_label)
        layout.addWidget(top)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.history = QListWidget()
        self.history.setSpacing(4)
        splitter.addWidget(self.history)
        side = QFrame(objectName="panel")
        side.setMinimumWidth(180)
        side.setMaximumWidth(260)
        side_layout = QVBoxLayout(side)
        side_layout.addWidget(QLabel("SISTEMAS", objectName="muted"))
        for name in SUBSYSTEMS:
            row = QHBoxLayout()
            row.addWidget(QLabel(name))
            label = QLabel("OFFLINE")
            label.setAlignment(Qt.AlignmentFlag.AlignRight)
            self._repolish(label, "error")
            self._statuses[name] = label
            row.addWidget(label)
            side_layout.addLayout(row)
        side_layout.addStretch()
        self.active_app = QLabel("App ativo: —", objectName="muted")
        self.current_project = QLabel("Projeto: —", objectName="muted")
        self.active_app.setWordWrap(True)
        self.current_project.setWordWrap(True)
        side_layout.addWidget(self.active_app)
        side_layout.addWidget(self.current_project)
        self.latency = QLabel("Latência: —", objectName="muted")
        side_layout.addWidget(self.latency)
        splitter.addWidget(side)
        splitter.setStretchFactor(0, 1)
        splitter.setSizes([850, 220])
        layout.addWidget(splitter, 1)

        footer = QFrame(objectName="panel")
        footer_layout = QHBoxLayout(footer)
        self.input = CommandInput()
        self.input.setPlaceholderText("Digite um pedido… (Shift+Enter para nova linha)")
        self.input.setMaximumHeight(90)
        footer_layout.addWidget(self.input, 1)
        self.mic = QPushButton("Microfone")
        self.mic.setCheckable(True)
        self.mic.setChecked(True)
        self.stop = QPushButton("Parar voz")
        self.send = QPushButton("Enviar", objectName="send")
        footer_layout.addWidget(self.mic)
        footer_layout.addWidget(self.stop)
        footer_layout.addWidget(self.send)
        layout.addWidget(footer)
        self.setCentralWidget(root)

    def _connect(self) -> None:
        self.send.clicked.connect(self._submit)
        self.input.submitted.connect(self._submit)
        self.mic.toggled.connect(self.controller.toggle_microphone)
        self.stop.clicked.connect(self.controller.stop_speech)
        self.controller.message_added.connect(self.add_message)
        self.controller.state_changed.connect(self.set_state)
        self.controller.subsystem_changed.connect(self.set_subsystem)
        self.controller.busy_changed.connect(self.set_busy)
        self.controller.confirmation_required.connect(self.confirm_action)
        self.controller.backend_latency.connect(lambda value: self.latency.setText(f"Latência: {value:.0f} ms"))
        self.controller.context_changed.connect(self.set_context)
        self.controller.diagnostics_received.connect(self.show_diagnostics)

    def _submit(self) -> None:
        text = self.input.toPlainText().strip()
        if text:
            self.input.clear()
            self.controller.submit_text(text)

    def add_message(self, message: UIMessage) -> None:
        if self.history.count() >= 100:
            self.history.takeItem(0)
        appearance = {
            MessageRole.USER: ("Você", "user"), MessageRole.ISABELLA: ("Isabella", "assistant"),
            MessageRole.SYSTEM: ("Sistema", "system"), MessageRole.ERROR: ("Erro", "error"),
        }
        name, object_name = appearance[message.role]
        stamp = message.timestamp.strftime("%H:%M")
        label = QLabel(f"<b>{name}</b> <small>{stamp}</small><br>{html.escape(message.text)}")
        label.setObjectName(object_name)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        item = QListWidgetItem()
        item.setSizeHint(label.sizeHint())
        self.history.addItem(item)
        self.history.setItemWidget(item, label)
        self.history.scrollToBottom()

    def set_state(self, state: str) -> None:
        self.state_label.setText(state)
        self._repolish(self.state_label, "error" if state == "ERROR" else "ok" if state == "IDLE" else "busy")

    def set_subsystem(self, name: str, status: str) -> None:
        label = self._statuses.get(name)
        if label:
            label.setText(status)
            kind = "ok" if status in {"ONLINE", "READY"} else "busy" if status in {"LOADING", "BUSY", "DEGRADED"} else "error"
            self._repolish(label, kind)

    def set_busy(self, busy: bool) -> None:
        self.send.setDisabled(busy)
        self.input.setDisabled(busy)
        if not busy:
            self.input.setFocus()

    def set_context(self, snapshot) -> None:
        application = snapshot.active_application if snapshot.active_application != "unavailable" else "—"
        self.active_app.setText(f"App ativo: {application}")
        self.current_project.setText(f"Projeto: {snapshot.current_project or '—'}")

    def confirm_action(self, request) -> None:
        labels = {
            "system.shutdown": "Desligar computador?",
            "system.restart": "Reiniciar computador?",
            "system.sleep": "Suspender computador?",
            "system.shutdown_timer": "Agendar o desligamento do computador?",
        }
        description = labels.get(request.skill_id, f"Executar '{request.skill_id}'?")
        dialog = QMessageBox(self)
        dialog.setWindowTitle("AÇÃO CRÍTICA")
        dialog.setText(description)
        dialog.setIcon(QMessageBox.Icon.Warning)
        confirm_button = dialog.addButton("Confirmar", QMessageBox.ButtonRole.AcceptRole)
        cancel_button = dialog.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(cancel_button)
        dialog.exec()
        if dialog.clickedButton() is confirm_button:
            self.controller.confirm_critical(request)
        else:
            self.controller.cancel_critical()

    def show_diagnostics(self, report: dict) -> None:
        statuses = report.get("statuses", {})
        lines = [f"{name}: {item.get('status', 'UNKNOWN')}" for name, item in statuses.items()]
        metrics = report.get("metrics", {})
        lines.extend([
            "",
            f"CPU: {metrics.get('cpu_percent', 0):.1f}%",
            f"RAM do processo: {metrics.get('process_memory_mb', 0):.1f} MB",
            f"Threads: {metrics.get('thread_count', 0)}",
        ])
        self._diagnostics_dialog = QMessageBox(self)
        self._diagnostics_dialog.setWindowTitle("Diagnóstico técnico")
        self._diagnostics_dialog.setText(report.get("summary", "Diagnóstico concluído."))
        self._diagnostics_dialog.setDetailedText("\n".join(lines))
        self._diagnostics_dialog.setStandardButtons(QMessageBox.StandardButton.Close)
        self._diagnostics_dialog.show()

    @staticmethod
    def _repolish(label: QLabel, kind: str) -> None:
        label.setProperty("kind", kind)
        label.style().unpolish(label)
        label.style().polish(label)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self.controller.shutdown()
        event.accept()


def run_gui() -> int:
    startup_started = perf_counter()
    qt_app = QApplication.instance() or QApplication(sys.argv)
    backend = IsabellaApp()
    backend.start()
    event_bus = getattr(backend, "event_bus", None)
    brain = Brain.from_config(event_bus=event_bus) if event_bus else Brain.from_config()
    controller = InterfaceController(backend, brain)
    gui_started = perf_counter()
    window = IsabellaHUD(controller)
    gui_ms = (perf_counter() - gui_started) * 1000
    window.show()
    controller.start_services()
    total_ms = (perf_counter() - startup_started) * 1000
    startup_metrics = getattr(backend, "startup_metrics", {})
    startup_metrics["gui_ms"] = gui_ms
    startup_metrics["total_ms"] = total_ms
    logging.getLogger("PERFORMANCE").info(
        "startup gui_ms=%.2f llm_initialization_ms=%.2f voice_ms=%.2f tts_ms=%.2f total_ms=%.2f",
        gui_ms, getattr(controller.brain, "startup_metrics", {}).get("llm_initialization_ms", 0.0),
        startup_metrics.get("voice_ms", 0.0), startup_metrics.get("tts_ms", 0.0), total_ms,
    )
    return qt_app.exec()
