"""Compact engineering window; the operational HUD remains independent."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from .panels import LogsPanel, OverviewPanel, TablePanel


class ControlCenterWindow(QMainWindow):
    def __init__(self, controller):
        super().__init__(); self.controller = controller
        self.setWindowTitle("I.S.A.B.E.L.L.A. — Engineering Control Center")
        self.resize(1050, 720)
        root = QWidget(); layout = QVBoxLayout(root)
        top = QHBoxLayout()
        title = QLabel("ENGINEERING CONTROL CENTER · somente leitura")
        self.admin = QCheckBox("Modo administrativo")
        self.admin.toggled.connect(controller.set_administrative)
        top.addWidget(title); top.addStretch(); top.addWidget(self.admin)
        self.mode_selector = QComboBox()
        self.mode_selector.currentTextChanged.connect(self._set_mode)
        top.addWidget(self.mode_selector)
        layout.addLayout(top)
        self.tabs = QTabWidget(); layout.addWidget(self.tabs); self.setCentralWidget(root)
        self.overview = OverviewPanel(); self.tabs.addTab(self.overview, "Visão geral")
        self.intelligence = TablePanel(); self.tabs.addTab(self.intelligence, "Intelligence")
        self.skills = TablePanel(); self.tabs.addTab(self.skills, "Skills")
        self.security = TablePanel(); self.tabs.addTab(self.security, "Security")
        self.memory = TablePanel(); self.tabs.addTab(self._memory_tab(), "Memory")
        self.events = TablePanel(); self.tabs.addTab(self.events, "Event Bus")
        self.agents = TablePanel(); self.tabs.addTab(self.agents, "Agents")
        self.knowledge = TablePanel(); self.tabs.addTab(self.knowledge, "Knowledge")
        self.digital_twins = TablePanel(); self.tabs.addTab(self.digital_twins, "Digital Twin")
        self.automations = TablePanel(); self.tabs.addTab(self._automation_tab(), "Automations")
        self.scheduler = TablePanel(); self.tabs.addTab(self._scheduler_tab(), "Scheduler")
        self.nodes = TablePanel(); self.tabs.addTab(self.nodes, "Nodes")
        self.home = TablePanel(); self.tabs.addTab(self.home, "Home")
        self.logs = LogsPanel(controller); self.tabs.addTab(self.logs, "Logs")
        self.services = QComboBox(); self.services.addItems(["API", "MCP", "Nodes", "Transport", "Home", "Voice Input", "Voice Output"])
        restart = QPushButton("Reiniciar serviço selecionado"); restart.clicked.connect(self._restart)
        footer = QHBoxLayout(); footer.addWidget(self.services); footer.addWidget(restart); footer.addStretch()
        layout.addLayout(footer)
        controller.snapshot_ready.connect(self.update_snapshot)
        controller.error.connect(lambda text: self.statusBar().showMessage(text, 8000))
        controller.start()

    def _memory_tab(self):
        box = QWidget(); layout = QVBoxLayout(box); controls = QHBoxLayout()
        self.memory_search = QLineEdit(); self.memory_search.setPlaceholderText("Pesquisar memória")
        search = QPushButton("Pesquisar"); search.clicked.connect(self._search_memory)
        delete = QPushButton("Excluir selecionada"); delete.clicked.connect(self._delete_memory)
        controls.addWidget(self.memory_search); controls.addWidget(search); controls.addWidget(delete)
        layout.addLayout(controls); layout.addWidget(self.memory); return box

    def _automation_tab(self):
        box = QWidget(); layout = QVBoxLayout(box); controls = QHBoxLayout()
        for label, enabled in (("Ativar", True), ("Desativar", False)):
            button = QPushButton(label); button.clicked.connect(lambda _=False, value=enabled: self._automation(value)); controls.addWidget(button)
        layout.addLayout(controls); layout.addWidget(self.automations); return box

    def _scheduler_tab(self):
        box = QWidget(); layout = QVBoxLayout(box); button = QPushButton("Cancelar selecionada")
        button.clicked.connect(self._cancel_task); layout.addWidget(button); layout.addWidget(self.scheduler); return box

    def _selected(self, table, key):
        row = table.currentRow()
        for column in range(table.columnCount()):
            if table.horizontalHeaderItem(column).text() == key and row >= 0:
                return table.item(row, column).text()
        return None

    def _guard(self, callback):
        try: callback(); self.controller.refresh()
        except Exception as exc: QMessageBox.warning(self, "Ação não executada", str(exc))

    def _search_memory(self): self.memory.update_rows(self.controller.search_memory(self.memory_search.text()))
    def _delete_memory(self):
        key = self._selected(self.memory, "key")
        if key: self._guard(lambda: self.controller.delete_memory(key, self._selected(self.memory, "type")))
    def _automation(self, enabled):
        item = self._selected(self.automations, "id")
        if item: self._guard(lambda: self.controller.set_automation_enabled(item, enabled))
    def _cancel_task(self):
        item = self._selected(self.scheduler, "id")
        if item: self._guard(lambda: self.controller.cancel_task(item))
    def _restart(self): self._guard(lambda: self.controller.restart_service(self.services.currentText()))
    def _set_mode(self, mode):
        if mode and not self.mode_selector.signalsBlocked():
            self._guard(lambda: self.controller.set_mode(mode))

    def update_snapshot(self, snapshot):
        self.mode_selector.blockSignals(True)
        if self.mode_selector.count() == 0: self.mode_selector.addItems(snapshot.available_modes)
        self.mode_selector.setCurrentText(snapshot.current_mode)
        self.mode_selector.blockSignals(False)
        self.overview.update_data(snapshot.overview, snapshot.metrics)
        self.intelligence.update_rows([snapshot.intelligence]); self.skills.update_rows(snapshot.skills)
        self.security.update_rows([snapshot.security]); self.memory.update_rows(snapshot.memory)
        self.events.update_rows(snapshot.events); self.automations.update_rows(snapshot.automations)
        self.agents.update_rows(snapshot.agents)
        self.knowledge.update_rows(snapshot.knowledge)
        self.digital_twins.update_rows(snapshot.digital_twins)
        self.scheduler.update_rows(snapshot.scheduler); self.nodes.update_rows(snapshot.nodes)
        self.home.update_rows([snapshot.home])

    def closeEvent(self, event):  # noqa: N802
        self.controller.shutdown(); event.accept()
