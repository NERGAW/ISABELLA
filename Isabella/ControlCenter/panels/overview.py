from PySide6.QtWidgets import QPlainTextEdit


class OverviewPanel(QPlainTextEdit):
    def __init__(self):
        super().__init__()
        self.setReadOnly(True)

    def update_data(self, statuses, metrics):
        lines = [f"{name}: {status}" for name, status in sorted(statuses.items())]
        lines += ["", "MÉTRICAS"] + [f"{name}: {value}" for name, value in sorted(metrics.items())]
        self.setPlainText("\n".join(lines))
