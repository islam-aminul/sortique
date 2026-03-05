"""Destination directory selector with free-space indicator."""

from __future__ import annotations

import os
import shutil

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class DestinationSelectorWidget(QWidget):
    """Group box for choosing a destination directory and showing free space.

    Emits ``destination_changed(str)`` when a new path is selected.
    """

    destination_changed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._path: str = ""
        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        group = QGroupBox("Destination Directory")
        layout = QVBoxLayout(group)
        layout.setSpacing(6)

        # Path row
        path_row = QHBoxLayout()
        self._path_label = QLabel("No destination selected")
        self._path_label.setStyleSheet("color: #888;")
        self._path_label.setWordWrap(True)
        self._btn_browse = QPushButton("Browse…")
        path_row.addWidget(self._path_label, 1)
        path_row.addWidget(self._btn_browse)
        layout.addLayout(path_row)

        # Space bar
        self._space_bar = QProgressBar()
        self._space_bar.setRange(0, 100)
        self._space_bar.setTextVisible(False)
        self._space_bar.setFixedHeight(8)
        self._space_bar.setVisible(False)
        layout.addWidget(self._space_bar)

        self._space_label = QLabel()
        self._space_label.setStyleSheet("color: #888; font-size: 11px;")
        self._space_label.setVisible(False)
        layout.addWidget(self._space_label)

        outer.addWidget(group)

        self._btn_browse.clicked.connect(self._browse)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Select Destination Directory", self._path or ""
        )
        if not path:
            return
        self._path = path
        self._path_label.setText(path)
        self._path_label.setStyleSheet("")
        self.refresh_space()
        self.destination_changed.emit(path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def destination_dir(self) -> str:
        """Return the currently selected destination path."""
        return self._path

    def refresh_space(self) -> None:
        """Re-query disk usage and update the space bar."""
        if not self._path or not os.path.isdir(self._path):
            self._space_bar.setVisible(False)
            self._space_label.setVisible(False)
            return

        try:
            usage = shutil.disk_usage(self._path)
        except OSError:
            self._space_bar.setVisible(False)
            self._space_label.setVisible(False)
            return

        pct_used = int(usage.used / usage.total * 100) if usage.total else 0
        pct_free = 100 - pct_used

        self._space_bar.setValue(pct_used)
        self._space_bar.setVisible(True)

        self._space_label.setText(
            f"{_fmt_bytes(usage.free)} free of {_fmt_bytes(usage.total)}"
        )
        self._space_label.setVisible(True)

        # Colour: green ≥20 %, yellow 5–20 %, red <5 %
        if pct_free >= 20:
            colour = "#4caf50"
        elif pct_free >= 5:
            colour = "#ff9800"
        else:
            colour = "#f44336"

        self._space_bar.setStyleSheet(
            f"QProgressBar::chunk {{ background-color: {colour}; border-radius: 3px; }}"
            "QProgressBar { background: #3a3a3a; border-radius: 3px; border: none; }"
        )

    def set_controls_enabled(self, enabled: bool) -> None:
        self._btn_browse.setEnabled(enabled)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
