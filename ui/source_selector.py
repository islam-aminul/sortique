"""Source directory selector widget."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class SourceSelectorWidget(QWidget):
    """A group box for managing the list of source directories.

    Emits ``sources_changed(list[str])`` whenever the list is modified.
    """

    sources_changed = Signal(list)  # list[str]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        group = QGroupBox("Source Directories")
        layout = QVBoxLayout(group)
        layout.setSpacing(6)

        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self._list.setMinimumHeight(110)
        layout.addWidget(self._list)

        btn_row = QHBoxLayout()
        self._btn_add = QPushButton("Add Folder")
        self._btn_remove = QPushButton("Remove Selected")
        self._btn_remove.setEnabled(False)
        btn_row.addWidget(self._btn_add)
        btn_row.addWidget(self._btn_remove)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._info_label = QLabel()
        self._info_label.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self._info_label)

        outer.addWidget(group)

        self._btn_add.clicked.connect(self._add_folder)
        self._btn_remove.clicked.connect(self._remove_selected)
        self._list.itemSelectionChanged.connect(
            lambda: self._btn_remove.setEnabled(
                bool(self._list.selectedItems())
            )
        )

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _add_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Select Source Directory", ""
        )
        if not path:
            return
        existing = self.source_dirs()
        if path in existing:
            return
        self._list.addItem(path)
        self.sources_changed.emit(self.source_dirs())

    def _remove_selected(self) -> None:
        for item in reversed(self._list.selectedItems()):
            self._list.takeItem(self._list.row(item))
        self.sources_changed.emit(self.source_dirs())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def source_dirs(self) -> list[str]:
        """Return the current list of source directories."""
        return [self._list.item(i).text() for i in range(self._list.count())]

    def set_scan_info(self, count: int, total_bytes: int) -> None:
        """Display file count and total size discovered by the scanner."""
        self._info_label.setText(
            f"{count:,} files found  ·  {_fmt_bytes(total_bytes)}"
        )

    def clear_scan_info(self) -> None:
        self._info_label.clear()

    def set_controls_enabled(self, enabled: bool) -> None:
        """Enable or disable add/remove controls (e.g. during processing)."""
        self._list.setEnabled(enabled)
        self._btn_add.setEnabled(enabled)
        if enabled:
            self._btn_remove.setEnabled(bool(self._list.selectedItems()))
        else:
            self._btn_remove.setEnabled(False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
