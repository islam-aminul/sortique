"""Session history view — browse, inspect and undo past organise sessions."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sortique.constants import SessionState
from sortique.ui.workers import UndoWorker

if TYPE_CHECKING:
    from sortique.data.models import Session
    from sortique.factory import AppFactory
    from sortique.service.undo_manager import UndoResult


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# SessionState → (display text, colour)
_STATUS_STYLE: dict[SessionState, tuple[str, str]] = {
    SessionState.COMPLETED:   ("Completed",   "#4caf50"),
    SessionState.ERROR:       ("Error",       "#f44336"),
    SessionState.UNDONE:      ("Undone",      "#888888"),
    SessionState.STOPPED:     ("Stopped",     "#ff9800"),
    SessionState.PAUSED:      ("Paused",      "#ff9800"),
    SessionState.RUNNING:     ("Running",     "#5294e2"),
    SessionState.IN_PROGRESS: ("In Progress", "#5294e2"),
    SessionState.PENDING:     ("Pending",     "#888888"),
}

# Column indices
_COL_DATE      = 0
_COL_STATUS    = 1
_COL_SOURCES   = 2
_COL_DEST      = 3
_COL_PROCESSED = 4
_COL_DUPES     = 5
_COL_SPACE     = 6
_COL_DURATION  = 7

_HEADERS = [
    "Date", "Status", "Source Dirs", "Destination",
    "Processed", "Duplicates", "Space Saved", "Duration",
]

# UserRole slots on the Date column item
_ROLE_SORT_KEY = Qt.ItemDataRole.UserRole          # ISO date string for sorting
_ROLE_SESSION  = Qt.ItemDataRole.UserRole + 1      # Session object


# ---------------------------------------------------------------------------
# Sortable table item
# ---------------------------------------------------------------------------

class _SortableItem(QTableWidgetItem):
    """QTableWidgetItem that sorts by its UserRole value when set."""

    def __lt__(self, other: QTableWidgetItem) -> bool:
        v  = self.data(Qt.ItemDataRole.UserRole)
        ov = other.data(Qt.ItemDataRole.UserRole)
        if v is not None and ov is not None:
            try:
                return v < ov
            except TypeError:
                pass
        return super().__lt__(other)


# ---------------------------------------------------------------------------
# SessionHistoryView
# ---------------------------------------------------------------------------

class SessionHistoryView(QWidget):
    """Shows past sessions with stats, detail inspection, and undo capability."""

    #: Emitted when the user clicks "Resume Session"; carries the session_id.
    resume_requested = Signal(str)

    def __init__(self, factory: AppFactory, parent=None) -> None:
        super().__init__(parent)
        self._factory = factory
        self._sessions: list[Session] = []
        self._undo_worker: UndoWorker | None = None
        self._build_ui()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def showEvent(self, event) -> None:
        """Reload sessions each time the view is shown."""
        super().showEvent(event)
        self.refresh()

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
        splitter.addWidget(self._build_table())
        splitter.addWidget(self._build_detail_panel())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([580, 320])

        root.addWidget(splitter, 1)

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(20, 12, 16, 12)

        title = QLabel("Sessions")
        font = title.font()
        font.setPointSize(14)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)
        layout.addStretch()

        self._btn_refresh = QPushButton("Refresh")
        self._btn_refresh.clicked.connect(self.refresh)
        layout.addWidget(self._btn_refresh)

        return bar

    def _build_table(self) -> QWidget:
        self._table = QTableWidget(0, len(_HEADERS))
        self._table.setHorizontalHeaderLabels(_HEADERS)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.setSortingEnabled(True)

        hdr = self._table.horizontalHeader()
        for col in (_COL_DATE, _COL_STATUS, _COL_PROCESSED,
                    _COL_DUPES, _COL_SPACE, _COL_DURATION):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(_COL_SOURCES, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(_COL_DEST,    QHeaderView.ResizeMode.Stretch)

        self._table.currentRowChanged.connect(self._on_row_changed)
        return self._table

    def _build_detail_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(260)
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Stack: 0 = placeholder, 1 = scrollable detail
        self._detail_stack = QStackedWidget()
        outer.addWidget(self._detail_stack, 1)

        # Page 0 — placeholder
        ph = QWidget()
        ph_layout = QVBoxLayout(ph)
        ph_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl = QLabel("Select a session\nto view details")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet("color: #666; font-size: 13px;")
        ph_layout.addWidget(lbl)
        self._detail_stack.addWidget(ph)

        # Page 1 — scrollable detail content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(16, 16, 16, 8)
        body_layout.setSpacing(12)

        stats_group = QGroupBox("Session Details")
        self._stats_form = QFormLayout(stats_group)
        self._stats_form.setHorizontalSpacing(16)
        self._stats_form.setVerticalSpacing(5)
        self._stats_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        body_layout.addWidget(stats_group)

        src_group = QGroupBox("Source Directories")
        src_layout = QVBoxLayout(src_group)
        src_layout.setContentsMargins(8, 8, 8, 8)
        self._src_list = QListWidget()
        self._src_list.setMaximumHeight(90)
        self._src_list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._src_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._src_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        src_layout.addWidget(self._src_list)
        body_layout.addWidget(src_group)

        body_layout.addStretch()
        scroll.setWidget(body)
        self._detail_stack.addWidget(scroll)

        # Action buttons — below the stack, always visible
        outer.addWidget(_h_line())
        outer.addWidget(self._build_action_buttons())

        return panel

    def _build_action_buttons(self) -> QWidget:
        bar = QWidget()
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(16, 10, 16, 14)
        layout.setSpacing(6)

        self._btn_undo = QPushButton("Undo Session")
        self._btn_undo.setEnabled(False)
        self._btn_undo.clicked.connect(self._on_undo)

        self._btn_resume = QPushButton("Resume Session")
        self._btn_resume.setEnabled(False)
        self._btn_resume.clicked.connect(self._on_resume)

        self._btn_archive = QPushButton("Delete Session Record")
        self._btn_archive.setEnabled(False)
        self._btn_archive.clicked.connect(self._on_archive)

        layout.addWidget(self._btn_undo)
        layout.addWidget(self._btn_resume)
        layout.addWidget(self._btn_archive)

        return bar

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Reload all sessions from the database and repopulate the table."""
        try:
            self._sessions = self._factory.db.list_sessions()
        except Exception as exc:
            QMessageBox.critical(self, "Load Error", str(exc))
            return
        self._populate_table()

    def _populate_table(self) -> None:
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)

        for row_idx, session in enumerate(self._sessions):
            self._table.insertRow(row_idx)
            self._fill_row(row_idx, session)

        self._table.setSortingEnabled(True)
        self._table.sortItems(_COL_DATE, Qt.SortOrder.DescendingOrder)
        self._table.resizeColumnToContents(_COL_DATE)

        self._detail_stack.setCurrentIndex(0)
        self._update_action_buttons(None)

    def _fill_row(self, row: int, session: Session) -> None:
        stats = session.stats

        # Date — ISO string in UserRole for correct lexicographic sort.
        date_item = _SortableItem(_fmt_date(session.created_at))
        date_item.setData(_ROLE_SORT_KEY, session.created_at.isoformat())
        date_item.setData(_ROLE_SESSION,  session)
        self._table.setItem(row, _COL_DATE, date_item)

        # Status
        label, colour = _STATUS_STYLE.get(
            session.state, (session.state.value, "#888888")
        )
        status_item = QTableWidgetItem(label)
        status_item.setForeground(QColor(colour))
        self._table.setItem(row, _COL_STATUS, status_item)

        # Source dirs — basename of first dir, tooltip shows all
        dirs = session.source_dirs
        first = os.path.basename(dirs[0]) if dirs else "—"
        suffix = f"  (+{len(dirs) - 1} more)" if len(dirs) > 1 else ""
        src_item = QTableWidgetItem(first + suffix)
        src_item.setToolTip("\n".join(dirs))
        self._table.setItem(row, _COL_SOURCES, src_item)

        # Destination
        dest_item = QTableWidgetItem(session.destination_dir or "—")
        dest_item.setToolTip(session.destination_dir)
        self._table.setItem(row, _COL_DEST, dest_item)

        # Numeric columns (sortable by raw value)
        processed = stats.get("files_processed", 0)
        dupes     = stats.get("dupes_found",     0)
        space     = stats.get("space_saved",      0)
        dur       = stats.get("duration_seconds", 0.0)

        for col, raw, text in (
            (_COL_PROCESSED, processed, f"{processed:,}"),
            (_COL_DUPES,     dupes,     f"{dupes:,}"),
            (_COL_SPACE,     space,     _fmt_bytes(space)),
            (_COL_DURATION,  dur,       _fmt_duration(dur)),
        ):
            item = _SortableItem(text)
            item.setData(Qt.ItemDataRole.UserRole, raw)
            item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self._table.setItem(row, col, item)

    # ------------------------------------------------------------------
    # Row selection → detail panel
    # ------------------------------------------------------------------

    def _on_row_changed(self, row: int) -> None:
        session = self._session_at_row(row)
        if session is None:
            self._detail_stack.setCurrentIndex(0)
            self._update_action_buttons(None)
        else:
            self._populate_detail(session)

    def _session_at_row(self, row: int) -> Session | None:
        if row < 0:
            return None
        item = self._table.item(row, _COL_DATE)
        return item.data(_ROLE_SESSION) if item else None

    def _selected_session(self) -> Session | None:
        return self._session_at_row(self._table.currentRow())

    def _populate_detail(self, session: Session) -> None:
        stats = session.stats

        # Rebuild form rows.
        while self._stats_form.rowCount():
            self._stats_form.removeRow(0)

        sid_lbl = _detail_label(session.id[:8] + "…")
        sid_lbl.setToolTip(session.id)
        self._stats_form.addRow("Session ID:",       sid_lbl)
        self._stats_form.addRow("Created:",          _detail_label(_fmt_date(session.created_at)))
        self._stats_form.addRow("Updated:",          _detail_label(_fmt_date(session.updated_at)))
        self._stats_form.addRow("State:",            _detail_label(
            session.state.value.replace("_", " ").title()
        ))
        self._stats_form.addRow("Files processed:",  _detail_label(f"{stats.get('files_processed', 0):,}"))
        self._stats_form.addRow("Files skipped:",    _detail_label(f"{stats.get('files_skipped', 0):,}"))
        self._stats_form.addRow("Duplicates found:", _detail_label(f"{stats.get('dupes_found', 0):,}"))
        self._stats_form.addRow("Space saved:",      _detail_label(_fmt_bytes(stats.get("space_saved", 0))))
        self._stats_form.addRow("Duration:",         _detail_label(_fmt_duration(stats.get("duration_seconds", 0.0))))

        dest_lbl = _detail_label(
            _truncate(session.destination_dir, 36) if session.destination_dir else "—"
        )
        dest_lbl.setToolTip(session.destination_dir)
        self._stats_form.addRow("Destination:", dest_lbl)

        self._src_list.clear()
        for d in session.source_dirs:
            self._src_list.addItem(d)

        self._detail_stack.setCurrentIndex(1)
        self._update_action_buttons(session)

    def _update_action_buttons(self, session: Session | None) -> None:
        has = session is not None
        self._btn_archive.setEnabled(has)

        can_undo   = has and session.state == SessionState.COMPLETED
        can_resume = has and session.state in (SessionState.STOPPED, SessionState.ERROR)
        self._btn_undo.setEnabled(can_undo)
        self._btn_resume.setEnabled(can_resume)

    # ------------------------------------------------------------------
    # Undo
    # ------------------------------------------------------------------

    def _on_undo(self) -> None:
        session = self._selected_session()
        if session is None or session.state != SessionState.COMPLETED:
            return

        # Pre-flight check.
        try:
            verification = self._factory.undo_manager().verify(session.id)
        except Exception as exc:
            QMessageBox.critical(self, "Verification Failed", str(exc))
            return

        # Choose dialog severity based on safety check.
        if not verification.safe_to_proceed:
            msg = (
                f"⚠  Safety check failed: {verification.files_missing:,} of "
                f"{verification.total_files:,} destination files are missing.\n\n"
                f"Proceed with a force undo? Files that still exist will be deleted."
            )
            answer = QMessageBox.warning(
                self,
                "Undo Session — Safety Warning",
                msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            force = True
        else:
            missing_note = ""
            if verification.files_missing > 0:
                missing_note = (
                    f"\n⚠  {verification.files_missing:,} file(s) are already "
                    f"missing from the destination.\n"
                )
            msg = (
                f"This will permanently delete all {verification.files_present:,} "
                f"organised file(s) from:\n\n  {session.destination_dir}\n"
                f"{missing_note}\n"
                f"Approx. {_fmt_bytes(verification.bytes_to_free)} will be freed.\n\n"
                f"Continue?"
            )
            answer = QMessageBox.warning(
                self,
                "Undo Session",
                msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            force = False

        self._run_undo(session.id, force)

    def _run_undo(self, session_id: str, force: bool) -> None:
        self._btn_undo.setEnabled(False)
        self._btn_undo.setText("Undoing…")
        self._btn_resume.setEnabled(False)
        self._btn_archive.setEnabled(False)

        worker = UndoWorker(self._factory, session_id, force=force, parent=self)
        worker.finished.connect(self._on_undo_finished)
        worker.error.connect(self._on_undo_error)
        worker.finished.connect(worker.deleteLater)
        worker.error.connect(worker.deleteLater)
        self._undo_worker = worker
        worker.start()

    def _on_undo_finished(self, result: UndoResult) -> None:
        self._btn_undo.setText("Undo Session")
        self._undo_worker = None

        if result.success:
            QMessageBox.information(
                self,
                "Undo Complete",
                f"Successfully deleted {result.files_deleted:,} file(s) and "
                f"removed {result.folders_removed:,} empty folder(s).",
            )
        else:
            preview = "\n".join(result.errors[:5])
            if len(result.errors) > 5:
                preview += f"\n… and {len(result.errors) - 5} more"
            QMessageBox.warning(
                self,
                "Undo Completed with Errors",
                f"Deleted {result.files_deleted:,} file(s).\n\n"
                f"Errors:\n{preview}",
            )

        self.refresh()

    def _on_undo_error(self, msg: str) -> None:
        self._btn_undo.setText("Undo Session")
        self._undo_worker = None
        QMessageBox.critical(self, "Undo Failed", msg)
        self.refresh()

    # ------------------------------------------------------------------
    # Resume
    # ------------------------------------------------------------------

    def _on_resume(self) -> None:
        session = self._selected_session()
        if session is not None:
            self.resume_requested.emit(session.id)

    # ------------------------------------------------------------------
    # Archive (soft-delete record)
    # ------------------------------------------------------------------

    def _on_archive(self) -> None:
        session = self._selected_session()
        if session is None:
            return

        answer = QMessageBox.question(
            self,
            "Delete Session Record",
            "Remove this session record from the history?\n\n"
            "No files will be deleted — only the record is removed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        try:
            self._factory.db.archive_session(session.id)
        except Exception as exc:
            QMessageBox.critical(self, "Delete Failed", str(exc))
            return

        self.refresh()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_date(dt) -> str:
    """Format a datetime as 'Mar 15, 2024 2:30 PM' in local time."""
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


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _fmt_duration(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"


def _truncate(text: str, max_len: int) -> str:
    return text if len(text) <= max_len else "…" + text[-(max_len - 1):]


def _detail_label(text: str, tooltip: str = "") -> QLabel:
    lbl = QLabel(text)
    lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    lbl.setWordWrap(True)
    if tooltip:
        lbl.setToolTip(tooltip)
    return lbl


def _h_line() -> QWidget:
    line = QWidget()
    line.setFixedHeight(1)
    line.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    line.setStyleSheet("background: #3a3a3a;")
    return line
