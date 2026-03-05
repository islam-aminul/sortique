"""Sortique application entry point."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from sortique.factory import AppFactory
from sortique.ui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Sortique")
    factory = AppFactory()
    window = MainWindow(factory)
    window.show()
    sys.exit(app.exec())
