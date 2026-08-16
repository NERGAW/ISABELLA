from PySide6.QtWidgets import QTableWidget, QTableWidgetItem


class TablePanel(QTableWidget):
    def __init__(self):
        super().__init__()
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setSortingEnabled(True)

    def update_rows(self, rows):
        rows = list(rows or [])
        columns = sorted({key for row in rows for key in row})
        self.setSortingEnabled(False)
        self.setColumnCount(len(columns)); self.setHorizontalHeaderLabels(columns)
        self.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column_index, key in enumerate(columns):
                self.setItem(row_index, column_index, QTableWidgetItem(str(row.get(key, ""))))
        self.resizeColumnsToContents(); self.setSortingEnabled(True)
