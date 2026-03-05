"""Main application window for Sortique."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from PySide6.QtCore import QSettings, QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from sortique.factory import AppFactory


# ---------------------------------------------------------------------------
# Sidebar navigation items
# ---------------------------------------------------------------------------

_NAV_ITEMS = [
    ("Organize", "Organize View"),
    ("Sessions", "Sessions View"),
    ("Collection Review", "Collection Review View"),
    ("Settings", "Settings View"),
]


# ---------------------------------------------------------------------------
# Placeholder content views
# ---------------------------------------------------------------------------

def _make_placeholder(label_text: str) -> QWidget:
    """Return a simple centred placeholder widget."""
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

    label = QLabel(label_text)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    font = label.font()
    font.setPointSize(16)
    label.setFont(font)
    label.setStyleSheet("color: #888888;")

    layout.addWidget(label)
    return widget


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    """Top-level application window with sidebar navigation and status bar."""

    def __init__(self, factory: AppFactory, parent=None) -> None:
        super().__init__(parent)
        self._factory = factory
        self._session_start: float | None = None

        self.setWindowTitle("Sortique")
        self.setMinimumSize(QSize(900, 600))

        self._build_ui()
        self._build_status_bar()
        self._restore_geometry()

        # Elapsed-time ticker (updates every second when a session is active).
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick_elapsed)
        self._timer.start()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)

        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # --- Sidebar ---
        self._sidebar = QListWidget()
        self._sidebar.setFixedWidth(180)
        self._sidebar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._sidebar.setStyleSheet(
            """
            QListWidget {
                background: #2b2b2b;
                border: none;
                border-right: 1px solid #3a3a3a;
                outline: none;
            }
            QListWidget::item {
                color: #cccccc;
                padding: 14px 20px;
                font-size: 13px;
            }
            QListWidget::item:selected {
                background: #3d3d3d;
                color: #ffffff;
                border-left: 3px solid #5294e2;
            }
            QListWidget::item:hover:!selected {
                background: #333333;
            }
            """
        )

        for nav_label, _ in _NAV_ITEMS:
            item = QListWidgetItem(nav_label)
            self._sidebar.addItem(item)

        self._sidebar.setCurrentRow(0)
        self._sidebar.currentRowChanged.connect(self._on_nav_changed)

        # --- Content stack ---
        self._stack = QStackedWidget()
        for _, placeholder_text in _NAV_ITEMS:
            self._stack.addWidget(_make_placeholder(placeholder_text))

        root_layout.addWidget(self._sidebar)
        root_layout.addWidget(self._stack)

    def _build_status_bar(self) -> None:
        status_bar = QStatusBar()
        self.setStatusBar(status_bar)
        status_bar.setStyleSheet(
            "QStatusBar { background: #1e1e1e; color: #aaaaaa; font-size: 12px; }"
            "QStatusBar::item { border: none; }"
        )

        self._status_session = QLabel("Session: Idle")
        self._status_files = QLabel("Files: 0")
        self._status_elapsed = QLabel("Elapsed: --")

        for widget in (
            self._status_session,
            self._status_files,
            self._status_elapsed,
        ):
            widget.setStyleSheet("padding: 0 12px;")

        # Permanent widgets appear right-aligned on the status bar.
        status_bar.addPermanentWidget(self._status_session)
        status_bar.addPermanentWidget(_separator())
        status_bar.addPermanentWidget(self._status_files)
        status_bar.addPermanentWidget(_separator())
        status_bar.addPermanentWidget(self._status_elapsed)

    # ------------------------------------------------------------------
    # Slot — navigation
    # ------------------------------------------------------------------

    def _on_nav_changed(self, row: int) -> None:
        self._stack.setCurrentIndex(row)

    # ------------------------------------------------------------------
    # Slot — elapsed ticker
    # ------------------------------------------------------------------

    def _tick_elapsed(self) -> None:
        if self._session_start is not None:
            elapsed = int(time.monotonic() - self._session_start)
            h, remainder = divmod(elapsed, 3600)
            m, s = divmod(remainder, 60)
            self._status_elapsed.setText(f"Elapsed: {h:02d}:{m:02d}:{s:02d}")

    # ------------------------------------------------------------------
    # Public helpers for other views to drive the status bar
    # ------------------------------------------------------------------

    def set_session_state(self, state: str) -> None:
        self._status_session.setText(f"Session: {state}")

    def set_file_count(self, count: int) -> None:
        self._status_files.setText(f"Files: {count}")

    def start_elapsed_timer(self) -> None:
        self._session_start = time.monotonic()

    def stop_elapsed_timer(self) -> None:
        self._session_start = None
        self._status_elapsed.setText("Elapsed: --")

    # ------------------------------------------------------------------
    # Window geometry persistence
    # ------------------------------------------------------------------

    def _restore_geometry(self) -> None:
        settings = QSettings("Sortique", "Sortique")
        geometry = settings.value("mainWindow/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

    def closeEvent(self, event) -> None:
        settings = QSettings("Sortique", "Sortique")
        settings.setValue("mainWindow/geometry", self.saveGeometry())
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _separator() -> QWidget:
    """Thin vertical separator for the status bar."""
    sep = QWidget()
    sep.setFixedWidth(1)
    sep.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
    sep.setStyleSheet("background: #3a3a3a;")
    return sep
