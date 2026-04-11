"""Sortique application entry point."""

from __future__ import annotations

import os
import sys

from PySide6.QtWidgets import QApplication

from sortique.factory import AppFactory
from sortique.ui.main_window import MainWindow


def main() -> None:
    # Suppress missing OpenType support font warnings on Linux (commonly seen with PyInstaller)
    os.environ["QT_LOGGING_RULES"] = "qt.text.font.db=false"

    app = QApplication(sys.argv)
    app.setApplicationName("Sortique")
    factory = AppFactory()
    window = MainWindow(factory)
    window.show()
    sys.exit(app.exec())
