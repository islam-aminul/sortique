"""Collection review view — reclassify files stuck in Collection/ fallback."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sortique.constants import SessionState

if TYPE_CHECKING:
    from sortique.data.models import Session
    from sortique.factory import AppFactory
    from sortique.service.collection_review import ReviewSuggestion


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Known categories offered in the override combo box.
_CATEGORIES = [
    "Originals", "Screenshots", "Social Media", "Export",
    "Edited", "Videos", "Bursts", "Motion Photos",
    "Voice Notes", "Documents", "Collection",
]

_COL_FILENAME = 0
_COL_TYPE     = 1
_COL_SIZE     = 2
_COL_SUGGEST  = 3
_COL_CONF     = 4
_COL_REASON   = 5
_COL_OVERRIDE = 6

_HEADERS = [
    "Filename", "Type", "Size",
    "Suggested Category", "Confidence", "Reason", "Override",
]

_ROLE_SUGGESTION = Qt.ItemDataRole.UserRole


# ---------------------------------------------------------------------------
# Background workers (defined here to avoid a separate workers file)
# ---------------------------------------------------------------------------

class _LoadWorker(QThread):
    """Fetches review suggestions from CollectionReviewer in a background thread."""

    finished = Signal(list)   # list[ReviewSuggestion]
    error    = Signal(str)

    def __init__(
        self,
        factory: AppFactory,
        session_id: str,
        destination_dir: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._factory         = factory
        self._session_id      = session_id
        self._destination_dir = destination_dir

    def run(self) -> None:
        try:
            reviewer = self._factory.collection_reviewer(self._destination_dir)
            self.finished.emit(reviewer.get_review_items(self._session_id))
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))


class _ReclassifyWorker(QThread):
    """Runs reclassify_batch in a background thread."""

    finished = Signal(list)   # list[FileRecord]
    error    = Signal(str)

    def __init__(
        self,
        factory: AppFactory,
        pairs: list[tuple[str, str]],
        session_id: str,
        destination_dir: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._factory         = factory
        self._pairs           = pairs
        self._session_id      = session_id
        self._destination_dir = destination_dir

    def run(self) -> None:
        try:
            reviewer = self._factory.collection_reviewer(self._destination_dir)
            self.finished.emit(
                reviewer.reclassify_batch(self._pairs, self._session_id)
            )
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# CollectionReviewView
# ---------------------------------------------------------------------------

class CollectionReviewView(QWidget):
    """UI for reviewing and reclassifying files in Collection/ fallback."""

    def __init__(self, factory: AppFactory, parent=None) -> None:
        super().__init__(parent)
        self._factory = factory
        self._sessions: list[Session] = []
        self._suggestions: list[ReviewSuggestion] = []
        self._sessions_loaded = False
        self._load_worker: _LoadWorker | None = None
        self._reclassify_worker: _ReclassifyWorker | None = None
        self._build_ui()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def showEvent(self, event) -> None:
        """Load sessions the first time this view is shown."""
        super().showEvent(event)
        if not self._sessions_loaded:
            self._sessions_loaded = True
            self._load_sessions()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_toolbar())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_preview_panel())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([680, 240])

        root.addWidget(splitter, 1)

        self._status_label = QLabel()
        self._status_label.setStyleSheet(
            "padding: 4px 16px; color: #888; font-size: 11px;"
        )
        root.addWidget(self._status_label)

    # -- Toolbar --

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(20, 12, 16, 12)
        layout.setSpacing(12)

        title = QLabel("Collection Review")
        font = title.font()
        font.setPointSize(14)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)

        layout.addStretch()

        layout.addWidget(QLabel("Session:"))

        self._session_combo = QComboBox()
        self._session_combo.setMinimumWidth(300)
        self._session_combo.currentIndexChanged.connect(self._on_session_changed)
        layout.addWidget(self._session_combo)

        self._btn_refresh = QPushButton("Refresh")
        self._btn_refresh.clicked.connect(self._refresh)
        layout.addWidget(self._btn_refresh)

        return bar

    # -- Left panel (table + action bar) --

    def _build_left_panel(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._table = QTableWidget(0, len(_HEADERS))
        self._table.setHorizontalHeaderLabels(_HEADERS)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        # Sorting disabled: setCellWidget items don't follow row moves.
        self._table.setSortingEnabled(False)

        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(_COL_FILENAME, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(_COL_TYPE,     QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(_COL_SIZE,     QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(_COL_SUGGEST,  QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(_COL_CONF,     QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(_COL_REASON,   QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(_COL_OVERRIDE, QHeaderView.ResizeMode.ResizeToContents)
        hdr.resizeSection(_COL_CONF, 110)
        hdr.resizeSection(_COL_FILENAME, 190)

        self._table.currentCellChanged.connect(
            lambda row, _col, _prev_row, _prev_col: self._on_row_changed(row)
        )
        self._table.itemSelectionChanged.connect(self._update_action_buttons)

        layout.addWidget(self._table, 1)
        layout.addWidget(_h_line())
        layout.addWidget(self._build_action_bar())

        return widget

    def _build_action_bar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 10, 16, 12)
        layout.setSpacing(8)

        self._btn_accept_all = QPushButton("Accept All Suggestions")
        self._btn_accept_all.setToolTip("Apply all suggestions with confidence > 70%")
        self._btn_accept_all.setEnabled(False)
        self._btn_accept_all.clicked.connect(self._accept_all_high_confidence)

        self._btn_apply = QPushButton("Apply Selected")
        self._btn_apply.setToolTip("Reclassify selected rows using the Override column")
        self._btn_apply.setEnabled(False)
        self._btn_apply.clicked.connect(self._apply_selected)

        self._btn_skip = QPushButton("Skip")
        self._btn_skip.setToolTip(
            "Keep selected files in Collection and remove them from this list"
        )
        self._btn_skip.setEnabled(False)
        self._btn_skip.clicked.connect(self._skip_selected)

        layout.addWidget(self._btn_accept_all)
        layout.addWidget(self._btn_apply)
        layout.addWidget(self._btn_skip)
        layout.addStretch()

        return bar

    # -- Preview panel --

    def _build_preview_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(200)

        outer = QVBoxLayout(panel)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(12, 12, 12, 12)
        body_layout.setSpacing(10)

        # Thumbnail area
        self._thumb_label = QLabel("No preview")
        self._thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb_label.setMinimumHeight(180)
        self._thumb_label.setStyleSheet(
            "background: #2a2a2a; border-radius: 4px; color: #555; font-size: 11px;"
        )
        body_layout.addWidget(self._thumb_label)

        # Details group
        detail_group = QGroupBox("File Details")
        self._detail_layout = QVBoxLayout(detail_group)
        self._detail_layout.setSpacing(4)
        body_layout.addWidget(detail_group)

        body_layout.addStretch()
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        return panel

    # ------------------------------------------------------------------
    # Session loading
    # ------------------------------------------------------------------

    def _load_sessions(self) -> None:
        try:
            all_sessions = self._factory.db.list_sessions()
        except Exception as exc:
            QMessageBox.critical(self, "Load Error", str(exc))
            return

        # Only completed sessions can have Collection/ files to review.
        self._sessions = [
            s for s in all_sessions if s.state == SessionState.COMPLETED
        ]

        self._session_combo.blockSignals(True)
        self._session_combo.clear()

        if not self._sessions:
            self._session_combo.addItem("No completed sessions available")
            self._status_label.setText(
                "Complete an organise session first to review Collection files."
            )
        else:
            for s in self._sessions:
                dest_basename = (
                    os.path.basename(s.destination_dir) or s.destination_dir
                )
                label = f"{_fmt_date(s.created_at)}  —  {dest_basename}"
                self._session_combo.addItem(label)

        self._session_combo.blockSignals(False)

        if self._sessions:
            self._on_session_changed(0)

    def _on_session_changed(self, index: int) -> None:
        if 0 <= index < len(self._sessions):
            self._load_review_items(self._sessions[index])

    # ------------------------------------------------------------------
    # Review items loading
    # ------------------------------------------------------------------

    def _load_review_items(self, session: Session) -> None:
        self._set_busy(True, "Scanning Collection files…")

        worker = _LoadWorker(
            self._factory, session.id, session.destination_dir, parent=self
        )
        worker.finished.connect(self._on_items_loaded)
        worker.error.connect(self._on_load_error)
        worker.finished.connect(worker.deleteLater)
        worker.error.connect(worker.deleteLater)
        self._load_worker = worker
        worker.start()

    def _on_items_loaded(self, items: list[ReviewSuggestion]) -> None:
        self._suggestions = items
        self._populate_table(items)
        self._set_busy(False)
        self._load_worker = None

        count = len(items)
        if count == 0:
            self._status_label.setText(
                "No Collection files to review in this session."
            )
        else:
            high = sum(1 for s in items if s.confidence > 0.7)
            self._status_label.setText(
                f"{count} file(s) in Collection  "
                f"·  {high} high-confidence suggestion(s) (>70%)"
            )

    def _on_load_error(self, msg: str) -> None:
        self._set_busy(False)
        self._load_worker = None
        QMessageBox.critical(self, "Load Error", msg)

    # ------------------------------------------------------------------
    # Table population
    # ------------------------------------------------------------------

    def _populate_table(self, items: list[ReviewSuggestion]) -> None:
        self._table.setRowCount(0)
        self._clear_preview()

        # Sort by confidence descending so high-value items appear first.
        sorted_items = sorted(items, key=lambda s: -s.confidence)

        for row, sug in enumerate(sorted_items):
            self._table.insertRow(row)
            self._fill_row(row, sug)

        self._update_action_buttons()

    def _fill_row(self, row: int, sug: ReviewSuggestion) -> None:
        rec = sug.file_record

        # Filename — carries the ReviewSuggestion in UserRole
        fname_item = QTableWidgetItem(os.path.basename(rec.source_path))
        fname_item.setData(_ROLE_SUGGESTION, sug)
        fname_item.setToolTip(rec.source_path)
        self._table.setItem(row, _COL_FILENAME, fname_item)

        # Type
        self._table.setItem(
            row, _COL_TYPE,
            QTableWidgetItem(rec.file_type.value.capitalize()),
        )

        # Size
        size_item = QTableWidgetItem(_fmt_bytes(rec.file_size))
        size_item.setTextAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._table.setItem(row, _COL_SIZE, size_item)

        # Suggested category
        self._table.setItem(
            row, _COL_SUGGEST,
            QTableWidgetItem(sug.suggested_category),
        )

        # Confidence — QProgressBar widget (sorting is disabled so this is safe)
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(int(sug.confidence * 100))
        bar.setTextVisible(True)
        bar.setFormat(f"{sug.confidence:.0%}")
        bar.setFixedHeight(18)
        colour = _confidence_colour(sug.confidence)
        bar.setStyleSheet(
            f"QProgressBar {{ background: #3a3a3a; border: none; border-radius: 3px; }}"
            f"QProgressBar::chunk {{ background: {colour}; border-radius: 3px; }}"
        )
        self._table.setCellWidget(row, _COL_CONF, bar)

        # Reason
        self._table.setItem(
            row, _COL_REASON,
            QTableWidgetItem(sug.reason),
        )

        # Override QComboBox — pre-selected to suggestion
        combo = QComboBox()
        combo.addItems(_CATEGORIES)
        idx = combo.findText(sug.suggested_category)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._table.setCellWidget(row, _COL_OVERRIDE, combo)

    # ------------------------------------------------------------------
    # Row selection → preview
    # ------------------------------------------------------------------

    def _on_row_changed(self, row: int) -> None:
        sug = self._suggestion_at_row(row)
        if sug is None:
            self._clear_preview()
        else:
            self._show_preview(sug)

    def _suggestion_at_row(self, row: int) -> ReviewSuggestion | None:
        if row < 0:
            return None
        item = self._table.item(row, _COL_FILENAME)
        return item.data(_ROLE_SUGGESTION) if item else None

    def _show_preview(self, sug: ReviewSuggestion) -> None:
        rec = sug.file_record

        # Thumbnail
        pixmap = QPixmap(rec.source_path)
        if pixmap.isNull():
            self._thumb_label.clear()
            self._thumb_label.setText("No preview available")
        else:
            scaled = pixmap.scaled(
                220, 200,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._thumb_label.setPixmap(scaled)
            self._thumb_label.setText("")

        # Details
        self._clear_detail_widgets()

        date_str = (
            rec.date_value.strftime("%Y-%m-%d %H:%M")
            if rec.date_value else "—"
        )

        rows = [
            ("Path",        rec.source_path),
            ("Destination", rec.destination_path or "—"),
            ("Type",        rec.file_type.value.capitalize()),
            ("Size",        _fmt_bytes(rec.file_size)),
            ("Date",        date_str),
            ("Date source", rec.date_source.value),
            ("Suggestion",  f"{sug.suggested_category}  ({sug.confidence:.0%})"),
            ("Reason",      sug.reason),
        ]
        for label, value in rows:
            self._detail_layout.addWidget(_detail_row(label, value))

    def _clear_preview(self) -> None:
        self._thumb_label.clear()
        self._thumb_label.setText("No preview")
        self._clear_detail_widgets()

    def _clear_detail_widgets(self) -> None:
        while self._detail_layout.count():
            item = self._detail_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    # ------------------------------------------------------------------
    # Action button state
    # ------------------------------------------------------------------

    def _update_action_buttons(self) -> None:
        has_rows      = self._table.rowCount() > 0
        has_selection = bool(self._table.selectedItems())
        self._btn_accept_all.setEnabled(has_rows)
        self._btn_apply.setEnabled(has_selection)
        self._btn_skip.setEnabled(has_selection)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _accept_all_high_confidence(self) -> None:
        pairs: list[tuple[str, str]] = []
        for row in range(self._table.rowCount()):
            sug = self._suggestion_at_row(row)
            if sug and sug.confidence > 0.7 and sug.suggested_category != "Collection":
                pairs.append((sug.file_record.id, sug.suggested_category))

        if not pairs:
            QMessageBox.information(
                self,
                "Accept All Suggestions",
                "No high-confidence suggestions (confidence > 70%) found.",
            )
            return

        self._run_reclassify(pairs)

    def _apply_selected(self) -> None:
        seen: set[int] = set()
        pairs: list[tuple[str, str]] = []

        for item in self._table.selectedItems():
            row = item.row()
            if row in seen:
                continue
            seen.add(row)
            sug = self._suggestion_at_row(row)
            combo = self._table.cellWidget(row, _COL_OVERRIDE)
            if sug is None or combo is None:
                continue
            new_cat = combo.currentText()
            # Reclassify only when the override differs from the current category.
            if new_cat and new_cat != sug.file_record.category:
                pairs.append((sug.file_record.id, new_cat))

        if not pairs:
            QMessageBox.information(
                self,
                "Apply Selected",
                "No changes to apply — override categories match current categories.",
            )
            return

        self._run_reclassify(pairs)

    def _skip_selected(self) -> None:
        """Remove selected rows from the table without touching the database."""
        rows = sorted(
            {item.row() for item in self._table.selectedItems()},
            reverse=True,
        )
        for row in rows:
            self._table.removeRow(row)

        self._update_action_buttons()
        remaining = self._table.rowCount()
        self._status_label.setText(
            f"{remaining} file(s) remaining in review list."
        )

    # ------------------------------------------------------------------
    # Reclassification
    # ------------------------------------------------------------------

    def _selected_session(self) -> Session | None:
        idx = self._session_combo.currentIndex()
        return self._sessions[idx] if 0 <= idx < len(self._sessions) else None

    def _run_reclassify(self, pairs: list[tuple[str, str]]) -> None:
        session = self._selected_session()
        if session is None:
            return

        self._set_busy(True, f"Reclassifying {len(pairs)} file(s)…")

        worker = _ReclassifyWorker(
            self._factory, pairs, session.id, session.destination_dir, parent=self
        )
        worker.finished.connect(self._on_reclassify_finished)
        worker.error.connect(self._on_reclassify_error)
        worker.finished.connect(worker.deleteLater)
        worker.error.connect(worker.deleteLater)
        self._reclassify_worker = worker
        worker.start()

    def _on_reclassify_finished(self, results: list) -> None:
        self._reclassify_worker = None
        count = len(results)
        # Reload to reflect new categories (some rows may disappear).
        self._status_label.setText(
            f"Reclassified {count} file(s) successfully. Reloading…"
        )
        self._refresh()

    def _on_reclassify_error(self, msg: str) -> None:
        self._reclassify_worker = None
        self._set_busy(False)
        QMessageBox.critical(self, "Reclassification Failed", msg)

    def _refresh(self) -> None:
        session = self._selected_session()
        if session is not None:
            self._load_review_items(session)
        else:
            self._sessions_loaded = False
            self._load_sessions()

    # ------------------------------------------------------------------
    # Busy state
    # ------------------------------------------------------------------

    def _set_busy(self, busy: bool, status: str = "") -> None:
        for widget in (
            self._btn_refresh,
            self._session_combo,
            self._btn_accept_all,
            self._btn_apply,
            self._btn_skip,
        ):
            widget.setEnabled(not busy)

        if status:
            self._status_label.setText(status)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _confidence_colour(confidence: float) -> str:
    if confidence > 0.7:
        return "#4caf50"
    if confidence >= 0.3:
        return "#ff9800"
    return "#888888"


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _fmt_date(dt) -> str:
    local = dt.astimezone()
    hour12 = local.hour % 12 or 12
    ampm = "AM" if local.hour < 12 else "PM"
    return (
        local.strftime("%b ")
        + str(local.day)
        + local.strftime(", %Y ")
        + str(hour12)
        + local.strftime(":%M ")
        + ampm
    )


def _detail_row(label: str, value: str) -> QWidget:
    """One label+value row for the preview detail section."""
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)

    lbl = QLabel(f"<b>{label}:</b>")
    lbl.setFixedWidth(88)
    lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)

    val = QLabel(value)
    val.setWordWrap(True)
    val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

    layout.addWidget(lbl)
    layout.addWidget(val, 1)
    return row


def _h_line() -> QWidget:
    line = QWidget()
    line.setFixedHeight(1)
    line.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    line.setStyleSheet("background: #3a3a3a;")
    return line
