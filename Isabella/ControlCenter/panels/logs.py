from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget


class LogsPanel(QWidget):
    def __init__(self, controller):
        super().__init__(); self.controller = controller
        self.module = QLineEdit(); self.module.setPlaceholderText("Módulo")
        self.level = QLineEdit(); self.level.setPlaceholderText("Nível")
        self.search = QLineEdit(); self.search.setPlaceholderText("Buscar")
        button = QPushButton("Atualizar"); button.clicked.connect(self.refresh)
        filters = QHBoxLayout()
        for widget in (self.module, self.level, self.search, button): filters.addWidget(widget)
        self.output = QPlainTextEdit(); self.output.setReadOnly(True)
        layout = QVBoxLayout(self); layout.addLayout(filters); layout.addWidget(self.output)

    def refresh(self):
        self.output.setPlainText("\n".join(self.controller.read_logs(self.module.text(), self.level.text(), self.search.text())))
