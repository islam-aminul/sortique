"""Main application window for Sortique."""

from __future__ import annotations

import os
import sys
import time
from typing import TYPE_CHECKING

from PySide6.QtCore import QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from sortique.factory import AppFactory


# Resolve the resources/ directory whether running from source or a frozen
# PyInstaller bundle (where data files land in sys._MEIPASS).
if getattr(sys, "frozen", False):
    _RESOURCES_DIR = os.path.join(sys._MEIPASS, "resources")
else:
    _RESOURCES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "resources")

_ICON_PATH = os.path.join(_RESOURCES_DIR, "app_icon.svg")


# ---------------------------------------------------------------------------
# Sidebar navigation items
# ---------------------------------------------------------------------------

_NAV_ITEMS = [
    "Organize",
    "Sessions",
    "Collection Review",
    "Settings",
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

        if os.path.isfile(_ICON_PATH):
            self.setWindowIcon(QIcon(_ICON_PATH))

        self._build_ui()
        self._build_status_bar()
        self._build_menubar()
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

        for nav_label in _NAV_ITEMS:
            item = QListWidgetItem(nav_label)
            self._sidebar.addItem(item)

        self._sidebar.setCurrentRow(0)
        self._sidebar.currentRowChanged.connect(self._on_nav_changed)

        # --- Content stack ---
        self._stack = QStackedWidget()

        # Index 0: Organize view
        from sortique.ui.organize_view import OrganizeView
        self._stack.addWidget(OrganizeView(self._factory))

        # Index 1: Sessions view
        from sortique.ui.session_history_view import SessionHistoryView
        sessions_view = SessionHistoryView(self._factory)
        sessions_view.resume_requested.connect(
            lambda _sid: self._sidebar.setCurrentRow(0)
        )
        self._stack.addWidget(sessions_view)

        # Index 2: Collection Review view
        from sortique.ui.collection_review_view import CollectionReviewView
        self._stack.addWidget(CollectionReviewView(self._factory))

        # Index 3: Settings view
        from sortique.ui.settings_view import SettingsView
        self._stack.addWidget(SettingsView(self._factory))

        root_layout.addWidget(self._sidebar)
        root_layout.addWidget(self._stack)

    def _build_menubar(self) -> None:
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("&File")
        exit_action = QAction("E&xit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # Help menu
        help_menu = menubar.addMenu("&Help")
        about_action = QAction("&About Sortique…", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _show_about(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("About Sortique")
        dlg.setFixedWidth(340)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(10)
        layout.setContentsMargins(24, 24, 24, 16)

        if os.path.isfile(_ICON_PATH):
            icon_label = QLabel()
            icon_label.setPixmap(QIcon(_ICON_PATH).pixmap(QSize(48, 48)))
            icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(icon_label)

        title = QLabel("<b>Sortique</b>  v1.0")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 16px;")
        layout.addWidget(title)

        desc = QLabel("A smart file organization tool.")
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setStyleSheet("color: #888888;")
        layout.addWidget(desc)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(dlg.accept)
        layout.addWidget(buttons)

        dlg.exec()

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
        if self._is_processing():
            reply = QMessageBox.warning(
                self,
                "Processing in Progress",
                "Sortique is currently organizing files.\n\nStop and exit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return

            # Ask the OrganizeView to stop gracefully.
            organize_view = self._stack.widget(0)
            if hasattr(organize_view, "_on_stop"):
                organize_view._on_stop()

        settings = QSettings("Sortique", "Sortique")
        settings.setValue("mainWindow/geometry", self.saveGeometry())
        super().closeEvent(event)

    def _is_processing(self) -> bool:
        """Return True if the OrganizeView has an active pipeline or scan."""
        organize_view = self._stack.widget(0)
        if organize_view is None:
            return False
        phase = getattr(organize_view, "_phase", None)
        if phase is None:
            return False
        from sortique.ui.organize_view import _Phase
        return phase in (
            _Phase.SCANNING, _Phase.PREVIEWING, _Phase.ORGANIZING, _Phase.PAUSED,
        )


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
