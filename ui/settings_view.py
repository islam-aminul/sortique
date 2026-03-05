"""Settings view — user-facing configuration panel."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from sortique.factory import AppFactory


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# (display label, [width, height]) — "Original" uses a sentinel large enough
# that no normal image will ever be downscaled.
_RESOLUTIONS: list[tuple[str, list[int]]] = [
    ("4K (3840×2160)",    [3840, 2160]),
    ("1080p (1920×1080)", [1920, 1080]),
    ("Original",          [99999, 99999]),
]

# Keys that this view reads and writes.
_UI_KEYS: tuple[str, ...] = (
    "jpeg_quality",
    "max_resolution",
    "threads",
    "verify_copies",
    "follow_symlinks",
    "musicbrainz_enabled",
)


# ---------------------------------------------------------------------------
# SettingsView
# ---------------------------------------------------------------------------

class SettingsView(QWidget):
    """Settings panel for common configuration options.

    Emits ``config_changed`` after a successful save so other views can
    react to updated settings.
    """

    config_changed = Signal()

    def __init__(self, factory: AppFactory, parent=None) -> None:
        super().__init__(parent)
        self._cfg = factory.config
        # Snapshot of the last persisted values for dirty-tracking.
        self._saved: dict = self._read_config()

        self._build_ui()
        self._load_widgets()
        self._refresh_dirty()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Scroll area holds all the groups.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(24, 20, 24, 20)
        body_layout.setSpacing(16)

        body_layout.addWidget(self._build_processing_group())
        body_layout.addWidget(self._build_safety_group())
        body_layout.addWidget(self._build_optional_group())
        body_layout.addWidget(self._build_advanced_group())
        body_layout.addStretch()

        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        # Fixed bottom bar (always visible).
        root.addWidget(_h_line())
        root.addWidget(self._build_bottom_bar())

    def _build_processing_group(self) -> QGroupBox:
        group = QGroupBox("Processing")
        form = QFormLayout(group)
        form.setHorizontalSpacing(20)
        form.setVerticalSpacing(10)

        self._jpeg_quality = QSpinBox()
        self._jpeg_quality.setRange(1, 100)
        self._jpeg_quality.setSuffix("  %")
        self._jpeg_quality.setFixedWidth(80)
        form.addRow("JPEG Quality:", self._jpeg_quality)

        self._max_resolution = QComboBox()
        for label, _ in _RESOLUTIONS:
            self._max_resolution.addItem(label)
        self._max_resolution.setFixedWidth(220)
        form.addRow("Max Resolution:", self._max_resolution)

        self._threads = QSpinBox()
        self._threads.setRange(1, 16)
        self._threads.setFixedWidth(80)
        form.addRow("Processing Threads:", self._threads)

        self._jpeg_quality.valueChanged.connect(self._on_widget_changed)
        self._max_resolution.currentIndexChanged.connect(self._on_widget_changed)
        self._threads.valueChanged.connect(self._on_widget_changed)

        return group

    def _build_safety_group(self) -> QGroupBox:
        group = QGroupBox("Safety")
        form = QFormLayout(group)
        form.setVerticalSpacing(8)

        self._verify_copies = QCheckBox("Verify copies after transfer")
        self._follow_symlinks = QCheckBox("Follow symbolic links")
        form.addRow(self._verify_copies)
        form.addRow(self._follow_symlinks)

        self._verify_copies.toggled.connect(self._on_widget_changed)
        self._follow_symlinks.toggled.connect(self._on_widget_changed)

        return group

    def _build_optional_group(self) -> QGroupBox:
        group = QGroupBox("Optional Features")
        layout = QVBoxLayout(group)
        layout.setSpacing(6)

        self._musicbrainz = QCheckBox("Enable MusicBrainz lookup")
        layout.addWidget(self._musicbrainz)

        self._musicbrainz_note = QLabel(
            "Audio file metadata will be sent to MusicBrainz external service."
        )
        self._musicbrainz_note.setStyleSheet(
            "color: #888; font-size: 11px; padding-left: 22px;"
        )
        self._musicbrainz_note.setWordWrap(True)
        self._musicbrainz_note.setVisible(False)
        layout.addWidget(self._musicbrainz_note)

        self._musicbrainz.toggled.connect(self._musicbrainz_note.setVisible)
        self._musicbrainz.toggled.connect(self._on_widget_changed)

        return group

    def _build_advanced_group(self) -> QGroupBox:
        group = QGroupBox("Advanced")
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._btn_open_config = QPushButton("Open config file…")
        self._btn_open_config.clicked.connect(self._open_config_file)
        btn_row.addWidget(self._btn_open_config)

        self._btn_reset = QPushButton("Reset to defaults")
        self._btn_reset.clicked.connect(self._reset_to_defaults)
        btn_row.addWidget(self._btn_reset)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        return group

    def _build_bottom_bar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(24, 10, 24, 16)
        layout.setSpacing(8)

        self._dirty_label = QLabel("● Unsaved changes")
        self._dirty_label.setStyleSheet("color: #ff9800; font-size: 11px;")
        self._dirty_label.setVisible(False)
        layout.addWidget(self._dirty_label)

        layout.addStretch()

        self._btn_cancel = QPushButton("Cancel")
        self._btn_cancel.setFixedWidth(90)
        self._btn_cancel.setEnabled(False)
        self._btn_cancel.clicked.connect(self._cancel)
        layout.addWidget(self._btn_cancel)

        self._btn_save = QPushButton("Save")
        self._btn_save.setFixedWidth(90)
        self._btn_save.setEnabled(False)
        self._btn_save.clicked.connect(self._save)
        layout.addWidget(self._btn_save)

        return bar

    # ------------------------------------------------------------------
    # Load / read helpers
    # ------------------------------------------------------------------

    def _read_config(self) -> dict:
        """Return effective values for every key this view manages."""
        return {k: self._cfg.get(k) for k in _UI_KEYS}

    def _load_widgets(self) -> None:
        """Populate widgets from the current effective config (blocks signals)."""
        cfg = self._cfg

        widgets = (
            self._jpeg_quality,
            self._max_resolution,
            self._threads,
            self._verify_copies,
            self._follow_symlinks,
            self._musicbrainz,
        )
        for w in widgets:
            w.blockSignals(True)

        self._jpeg_quality.setValue(cfg.jpeg_quality)

        res = list(cfg.max_resolution)
        res_idx = next(
            (i for i, (_, r) in enumerate(_RESOLUTIONS) if r == res),
            len(_RESOLUTIONS) - 1,   # unknown → "Original"
        )
        self._max_resolution.setCurrentIndex(res_idx)

        self._threads.setValue(cfg.threads)
        self._verify_copies.setChecked(cfg.verify_copies)
        self._follow_symlinks.setChecked(cfg.follow_symlinks)
        self._musicbrainz.setChecked(cfg.musicbrainz_enabled)
        self._musicbrainz_note.setVisible(cfg.musicbrainz_enabled)

        for w in widgets:
            w.blockSignals(False)

    def _read_widgets(self) -> dict:
        """Collect current widget values into a dict."""
        return {
            "jpeg_quality":        self._jpeg_quality.value(),
            "max_resolution":      _RESOLUTIONS[self._max_resolution.currentIndex()][1],
            "threads":             self._threads.value(),
            "verify_copies":       self._verify_copies.isChecked(),
            "follow_symlinks":     self._follow_symlinks.isChecked(),
            "musicbrainz_enabled": self._musicbrainz.isChecked(),
        }

    # ------------------------------------------------------------------
    # Dirty tracking
    # ------------------------------------------------------------------

    def _on_widget_changed(self) -> None:
        self._refresh_dirty()

    def _refresh_dirty(self) -> None:
        dirty = self._read_widgets() != self._saved
        self._dirty_label.setVisible(dirty)
        self._btn_save.setEnabled(dirty)
        self._btn_cancel.setEnabled(dirty)

    # ------------------------------------------------------------------
    # Button actions
    # ------------------------------------------------------------------

    def _save(self) -> None:
        values = self._read_widgets()

        # Validate via ConfigManager's own rules.
        try:
            from sortique.data.config_manager import ConfigManager
            for key, val in values.items():
                ConfigManager._validate(key, val)
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid Setting", str(exc))
            return

        self._cfg.save_user_config(values)
        self._saved = self._read_config()
        self._refresh_dirty()
        self.config_changed.emit()

    def _cancel(self) -> None:
        self._load_widgets()
        self._refresh_dirty()

    def _open_config_file(self) -> None:
        path = self._cfg._config_dir / "config.json"
        if not path.exists():
            # Write a stub so the editor has something to open.
            path.write_text("{}\n", encoding="utf-8")

        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            QMessageBox.warning(
                self,
                "Could Not Open File",
                f"Failed to open config file in the system default editor:\n\n{path}",
            )

    def _reset_to_defaults(self) -> None:
        answer = QMessageBox.question(
            self,
            "Reset to Defaults",
            "Reset all settings to their default values?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        # Pull the built-in defaults for the keys we manage.
        raw_defaults = {k: self._cfg._defaults.get(k) for k in _UI_KEYS}
        self._cfg.save_user_config(raw_defaults)
        self._saved = self._read_config()
        self._load_widgets()
        self._refresh_dirty()
        self.config_changed.emit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _h_line() -> QWidget:
    """Thin horizontal rule between the scroll area and the bottom bar."""
    line = QWidget()
    line.setFixedHeight(1)
    line.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    line.setStyleSheet("background: #3a3a3a;")
    return line
