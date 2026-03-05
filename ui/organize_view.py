"""Organize view — the primary screen of the Sortique application."""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from sortique.data.models import FileRecord
from sortique.ui.destination_selector import DestinationSelectorWidget
from sortique.ui.source_selector import SourceSelectorWidget
from sortique.ui.workers import DryRunWorker, PipelineWorker, ScanWorker

if TYPE_CHECKING:
    from sortique.engine.scanner import ScanResult
    from sortique.factory import AppFactory
    from sortique.service.dry_run import DryRunSummary
    from sortique.service.thread_pool import ProcessingProgress


# ---------------------------------------------------------------------------
# Internal state machine
# ---------------------------------------------------------------------------

class _Phase(enum.Enum):
    IDLE = "idle"
    SCANNING = "scanning"
    SCANNED = "scanned"
    PREVIEWING = "previewing"
    ORGANIZING = "organizing"
    PAUSED = "paused"
    DONE = "done"


# ---------------------------------------------------------------------------
# Dry-run results dialog
# ---------------------------------------------------------------------------

class DryRunDialog(QDialog):
    """Modal dialog that presents a DryRunSummary and asks the user to confirm."""

    def __init__(self, summary: DryRunSummary, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Preview Results")
        self.setMinimumWidth(480)
        self.setModal(True)
        self._build_ui(summary)

    def _build_ui(self, s: DryRunSummary) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Summary numbers
        summary_group = QGroupBox("Summary")
        form = QFormLayout(summary_group)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.addRow("Total files:", QLabel(f"{s.total_files:,}"))
        form.addRow("Files to copy:", QLabel(f"{s.files_to_copy:,}"))
        form.addRow("Files to skip:", QLabel(f"{s.files_to_skip:,}"))
        form.addRow("Duplicates found:", QLabel(f"{s.duplicates_found:,}"))
        form.addRow(
            "Estimated space needed:",
            QLabel(_fmt_bytes(s.estimated_space_bytes)),
        )
        if s.space_check is not None:
            if s.space_check.passes:
                space_text = (
                    f"OK  ({_fmt_bytes(s.space_check.available_bytes)} available)"
                )
                space_lbl = QLabel(space_text)
            else:
                shortfall = _fmt_bytes(s.space_check.shortfall_bytes)
                space_lbl = QLabel(f"Insufficient — {shortfall} short")
                space_lbl.setStyleSheet("color: #f44336; font-weight: bold;")
            form.addRow("Disk space:", space_lbl)
        layout.addWidget(summary_group)

        # Category breakdown
        if s.category_breakdown:
            cat_group = QGroupBox("Category Breakdown")
            cat_form = QFormLayout(cat_group)
            cat_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
            for cat, count in sorted(
                s.category_breakdown.items(), key=lambda kv: -kv[1]
            ):
                cat_form.addRow(f"{cat}:", QLabel(f"{count:,}"))
            layout.addWidget(cat_group)

        # Warnings
        if s.warnings:
            warn_group = QGroupBox("Warnings")
            warn_layout = QVBoxLayout(warn_group)
            warn_layout.setSpacing(4)
            for msg in s.warnings:
                lbl = QLabel(f"⚠  {msg}")
                lbl.setWordWrap(True)
                lbl.setStyleSheet("color: #ff9800;")
                warn_layout.addWidget(lbl)
            layout.addWidget(warn_group)

        # Buttons
        btn_box = QDialogButtonBox()
        self._proceed_btn = btn_box.addButton(
            "Proceed with Organize", QDialogButtonBox.ButtonRole.AcceptRole
        )
        btn_box.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)

        # Block proceed if there is definitely not enough space.
        if s.space_check is not None and not s.space_check.passes:
            self._proceed_btn.setEnabled(False)

        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)


# ---------------------------------------------------------------------------
# Organize view
# ---------------------------------------------------------------------------

class OrganizeView(QWidget):
    """Main organize view: source selection, destination, and action buttons."""

    def __init__(self, factory: AppFactory, parent=None) -> None:
        super().__init__(parent)
        self._factory = factory

        # Transient state
        self._phase = _Phase.IDLE
        self._scan_result: ScanResult | None = None
        self._file_records: list[FileRecord] = []
        self._session_id: str = ""

        # Active workers (kept alive while running)
        self._scan_worker: ScanWorker | None = None
        self._dryrun_worker: DryRunWorker | None = None
        self._pipeline_worker: PipelineWorker | None = None

        self._build_ui()
        self._apply_phase(_Phase.IDLE)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # Wrap everything in a scroll area so it never clips on small windows.
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        content = QWidget()
        scroll.setWidget(content)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

        main_layout = QVBoxLayout(content)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(14)

        # --- Source ---
        self._source_sel = SourceSelectorWidget()
        main_layout.addWidget(self._source_sel)

        # --- Destination ---
        self._dest_sel = DestinationSelectorWidget()
        main_layout.addWidget(self._dest_sel)

        # --- Action buttons ---
        main_layout.addWidget(self._build_action_bar())

        # --- Progress section (hidden until active) ---
        self._progress_group = self._build_progress_section()
        self._progress_group.setVisible(False)
        main_layout.addWidget(self._progress_group)

        main_layout.addStretch()

        # Signals from sub-widgets
        self._source_sel.sources_changed.connect(self._on_sources_changed)
        self._dest_sel.destination_changed.connect(self._on_destination_changed)

    def _build_action_bar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._btn_scan = QPushButton("Scan")
        self._btn_scan.setMinimumWidth(90)
        self._btn_preview = QPushButton("Preview")
        self._btn_preview.setMinimumWidth(90)
        self._btn_organize = QPushButton("Organize")
        self._btn_organize.setMinimumWidth(90)
        self._btn_pause = QPushButton("Pause")
        self._btn_pause.setMinimumWidth(90)
        self._btn_stop = QPushButton("Stop")
        self._btn_stop.setMinimumWidth(90)

        layout.addWidget(self._btn_scan)
        layout.addWidget(self._btn_preview)
        layout.addWidget(self._btn_organize)
        layout.addSpacing(16)
        layout.addWidget(self._btn_pause)
        layout.addWidget(self._btn_stop)
        layout.addStretch()

        self._btn_scan.clicked.connect(self._on_scan)
        self._btn_preview.clicked.connect(self._on_preview)
        self._btn_organize.clicked.connect(self._on_organize)
        self._btn_pause.clicked.connect(self._on_pause_resume)
        self._btn_stop.clicked.connect(self._on_stop)

        return bar

    def _build_progress_section(self) -> QGroupBox:
        group = QGroupBox("Progress")
        layout = QVBoxLayout(group)
        layout.setSpacing(6)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        layout.addWidget(self._progress_bar)

        self._current_file_label = QLabel("—")
        self._current_file_label.setStyleSheet("color: #888; font-size: 11px;")
        self._current_file_label.setWordWrap(True)
        layout.addWidget(self._current_file_label)

        self._stats_label = QLabel()
        self._stats_label.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self._stats_label)

        return group

    # ------------------------------------------------------------------
    # Phase management
    # ------------------------------------------------------------------

    def _apply_phase(self, phase: _Phase) -> None:
        self._phase = phase

        # Defaults — override per phase below
        scan_en = False
        prev_en = False
        org_en = False
        pause_vis = False
        stop_vis = False
        sel_en = True

        if phase == _Phase.IDLE:
            scan_en = True

        elif phase == _Phase.SCANNING:
            sel_en = False
            self._progress_group.setVisible(True)
            self._progress_bar.setRange(0, 0)  # indeterminate
            self._stats_label.clear()

        elif phase == _Phase.SCANNED:
            scan_en = True
            prev_en = True
            self._progress_bar.setRange(0, 100)
            self._progress_bar.setValue(100)

        elif phase == _Phase.PREVIEWING:
            sel_en = False
            self._progress_bar.setRange(0, 0)

        elif phase == _Phase.ORGANIZING:
            sel_en = False
            pause_vis = True
            stop_vis = True
            self._progress_bar.setRange(0, 100)
            self._btn_pause.setText("Pause")

        elif phase == _Phase.PAUSED:
            sel_en = False
            pause_vis = True
            stop_vis = True
            self._btn_pause.setText("Resume")

        elif phase == _Phase.DONE:
            scan_en = True
            self._progress_bar.setRange(0, 100)
            self._progress_bar.setValue(100)

        self._btn_scan.setEnabled(scan_en)
        self._btn_preview.setEnabled(prev_en)
        self._btn_organize.setEnabled(org_en)
        self._btn_pause.setVisible(pause_vis)
        self._btn_stop.setVisible(stop_vis)
        self._source_sel.set_controls_enabled(sel_en)
        self._dest_sel.set_controls_enabled(sel_en)

    # ------------------------------------------------------------------
    # Sub-widget slots
    # ------------------------------------------------------------------

    def _on_sources_changed(self, _dirs: list[str]) -> None:
        # Changing sources invalidates any previous scan.
        if self._phase not in (_Phase.SCANNING, _Phase.ORGANIZING, _Phase.PAUSED):
            self._reset_to_idle()

    def _on_destination_changed(self, _path: str) -> None:
        self._dest_sel.refresh_space()
        if self._phase not in (_Phase.SCANNING, _Phase.ORGANIZING, _Phase.PAUSED):
            self._reset_to_idle()

    # ------------------------------------------------------------------
    # Action button slots
    # ------------------------------------------------------------------

    def _on_scan(self) -> None:
        source_dirs = self._source_sel.source_dirs()
        if not source_dirs:
            QMessageBox.warning(self, "No Sources", "Please add at least one source directory.")
            return

        self._scan_result = None
        self._file_records = []
        self._session_id = ""
        self._source_sel.clear_scan_info()
        self._current_file_label.setText("Scanning…")
        self._stats_label.clear()
        self._apply_phase(_Phase.SCANNING)
        self._update_main_window(state="Scanning")

        worker = ScanWorker(self._factory, source_dirs, parent=self)
        worker.progress.connect(self._on_scan_progress)
        worker.finished.connect(self._on_scan_finished)
        worker.error.connect(self._on_scan_error)
        worker.finished.connect(worker.deleteLater)
        worker.error.connect(worker.deleteLater)
        self._scan_worker = worker
        worker.start()

    def _on_preview(self) -> None:
        if self._scan_result is None:
            return
        dest = self._dest_sel.destination_dir()
        if not dest:
            QMessageBox.warning(self, "No Destination", "Please select a destination directory.")
            return

        self._current_file_label.setText("Running preview analysis…")
        self._stats_label.clear()
        self._apply_phase(_Phase.PREVIEWING)
        self._update_main_window(state="Previewing")

        records = self._make_records_for_dryrun()
        worker = DryRunWorker(self._factory, records, dest, parent=self)
        worker.progress.connect(self._on_dryrun_progress)
        worker.finished.connect(self._on_dryrun_finished)
        worker.error.connect(self._on_dryrun_error)
        worker.finished.connect(worker.deleteLater)
        worker.error.connect(worker.deleteLater)
        self._dryrun_worker = worker
        worker.start()

    def _on_organize(self) -> None:
        # Should not be reachable via button (disabled); kept as safety guard.
        if not self._file_records or not self._session_id:
            return
        self._start_pipeline()

    def _on_pause_resume(self) -> None:
        if self._phase == _Phase.ORGANIZING:
            if self._pipeline_worker is not None:
                self._pipeline_worker.pause()
            self._apply_phase(_Phase.PAUSED)
            self._update_main_window(state="Paused")
            try:
                sm = self._factory.session_manager()
                from sortique.constants import SessionState
                sm.transition(self._session_id, SessionState.PAUSED)
            except Exception:
                pass

        elif self._phase == _Phase.PAUSED:
            if self._pipeline_worker is not None:
                self._pipeline_worker.resume()
            self._apply_phase(_Phase.ORGANIZING)
            self._update_main_window(state="Organizing")
            try:
                sm = self._factory.session_manager()
                from sortique.constants import SessionState
                sm.transition(self._session_id, SessionState.RUNNING)
            except Exception:
                pass

    def _on_stop(self) -> None:
        if self._pipeline_worker is not None:
            self._pipeline_worker.stop()
        try:
            sm = self._factory.session_manager()
            from sortique.constants import SessionState
            sm.transition(self._session_id, SessionState.STOPPED)
        except Exception:
            pass
        self._update_main_window(state="Stopped")
        # Worker will emit finished; cleanup happens there.

    # ------------------------------------------------------------------
    # Scan worker callbacks
    # ------------------------------------------------------------------

    def _on_scan_progress(self, count: int, path: str) -> None:
        self._current_file_label.setText(path)
        self._stats_label.setText(f"{count:,} files found so far…")

    def _on_scan_finished(self, result: ScanResult) -> None:
        self._scan_result = result
        count = len(result.files)
        self._source_sel.set_scan_info(count, result.total_bytes)
        self._current_file_label.setText(
            f"Scan complete — {count:,} files found"
        )
        self._stats_label.setText(
            f"Total size: {_fmt_bytes(result.total_bytes)}"
            f"  ·  Scan time: {result.scan_duration:.1f}s"
        )
        self._apply_phase(_Phase.SCANNED)
        self._update_main_window(state="Ready", file_count=count)

    def _on_scan_error(self, msg: str) -> None:
        self._apply_phase(_Phase.IDLE)
        self._current_file_label.setText("Scan failed.")
        self._update_main_window(state="Error")
        QMessageBox.critical(self, "Scan Error", msg)

    # ------------------------------------------------------------------
    # Dry-run worker callbacks
    # ------------------------------------------------------------------

    def _on_dryrun_progress(self, current: int, total: int) -> None:
        if total > 0:
            self._progress_bar.setRange(0, total)
            self._progress_bar.setValue(current)
        self._current_file_label.setText(
            f"Analysing file {current:,} of {total:,}…"
        )

    def _on_dryrun_finished(self, summary: DryRunSummary) -> None:
        self._apply_phase(_Phase.SCANNED)

        dlg = DryRunDialog(summary, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._commit_session_and_start(summary)

    def _on_dryrun_error(self, msg: str) -> None:
        self._apply_phase(_Phase.SCANNED)
        self._current_file_label.setText("Preview failed.")
        QMessageBox.critical(self, "Preview Error", msg)

    # ------------------------------------------------------------------
    # Pipeline worker callbacks
    # ------------------------------------------------------------------

    def _on_pipeline_progress(self, prog: ProcessingProgress) -> None:
        total = prog.total_files
        done = prog.processed
        if total > 0:
            self._progress_bar.setValue(int(done / total * 100))
        if prog.current_file:
            self._current_file_label.setText(prog.current_file)
        self._stats_label.setText(
            f"Processed: {done:,}  ·  Skipped: {prog.skipped:,}"
            f"  ·  Dupes: {prog.duplicates:,}  ·  Errors: {prog.errors:,}"
            f"  ·  {prog.files_per_second:.1f} files/s"
        )
        self._update_main_window(file_count=done)

    def _on_pipeline_finished(self, prog: ProcessingProgress) -> None:
        # Finalize session
        try:
            self._factory.session_manager().finalize_session(self._session_id)
        except Exception:
            pass

        self._apply_phase(_Phase.DONE)
        self._current_file_label.setText("Organize complete.")
        self._update_main_window(state="Complete")
        self._stop_elapsed()
        self._pipeline_worker = None

        # Desktop notification
        try:
            stats = self._factory.session_manager().get_session_stats(
                self._session_id
            )
            self._factory.notification_service().notify_completion(stats)
        except Exception:
            pass

        # Summary dialog
        self._show_completion_dialog(prog)

    def _on_pipeline_error(self, msg: str) -> None:
        try:
            sm = self._factory.session_manager()
            from sortique.constants import SessionState
            sm.transition(self._session_id, SessionState.ERROR)
        except Exception:
            pass

        self._apply_phase(_Phase.SCANNED)
        self._current_file_label.setText("Processing error.")
        self._update_main_window(state="Error")
        self._stop_elapsed()
        self._pipeline_worker = None

        try:
            self._factory.notification_service().notify_error(msg)
        except Exception:
            pass

        QMessageBox.critical(self, "Processing Error", msg)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _reset_to_idle(self) -> None:
        self._scan_result = None
        self._file_records = []
        self._session_id = ""
        self._source_sel.clear_scan_info()
        self._progress_group.setVisible(False)
        self._progress_bar.setValue(0)
        self._progress_bar.setRange(0, 100)
        self._current_file_label.clear()
        self._stats_label.clear()
        self._apply_phase(_Phase.IDLE)
        self._update_main_window(state="Idle", file_count=0)

    def _make_records_for_dryrun(self) -> list[FileRecord]:
        """Build temporary FileRecord objects for the dry-run (not persisted)."""
        records = []
        for sf in self._scan_result.files:
            records.append(
                FileRecord(
                    session_id="",
                    source_path=sf.path,
                    source_dir=sf.source_dir,
                    file_size=sf.size,
                )
            )
        return records

    def _commit_session_and_start(self, _summary: DryRunSummary) -> None:
        """Create a real session, persist records, and launch the pipeline."""
        source_dirs = self._source_sel.source_dirs()
        dest = self._dest_sel.destination_dir()

        try:
            sm = self._factory.session_manager()
            session = sm.create_session(source_dirs, dest)
            self._session_id = session.id

            # Transition PENDING → IN_PROGRESS
            from sortique.constants import SessionState
            sm.transition(self._session_id, SessionState.IN_PROGRESS)

            # Build and persist file records
            records = []
            for sf in self._scan_result.files:
                rec = FileRecord(
                    session_id=self._session_id,
                    source_path=sf.path,
                    source_dir=sf.source_dir,
                    file_size=sf.size,
                )
                self._factory.db.create_file_record(rec)
                records.append(rec)
            self._file_records = records

        except Exception as exc:
            QMessageBox.critical(self, "Session Error", str(exc))
            self._apply_phase(_Phase.SCANNED)
            return

        self._start_pipeline()

    def _start_pipeline(self) -> None:
        dest = self._dest_sel.destination_dir()
        self._progress_bar.setValue(0)
        self._current_file_label.setText("Starting…")
        self._stats_label.clear()
        self._apply_phase(_Phase.ORGANIZING)
        self._update_main_window(state="Organizing")
        self._start_elapsed()

        try:
            from sortique.constants import SessionState
            self._factory.session_manager().transition(
                self._session_id, SessionState.RUNNING
            )
        except Exception:
            pass

        worker = PipelineWorker(
            self._factory,
            self._file_records,
            dest,
            self._session_id,
            parent=self,
        )
        worker.progress.connect(self._on_pipeline_progress)
        worker.finished.connect(self._on_pipeline_finished)
        worker.error.connect(self._on_pipeline_error)
        worker.finished.connect(worker.deleteLater)
        worker.error.connect(worker.deleteLater)
        self._pipeline_worker = worker
        worker.start()

    def _show_completion_dialog(self, prog: ProcessingProgress) -> None:
        msg = (
            f"<b>Organize complete!</b><br><br>"
            f"Files processed: <b>{prog.processed:,}</b><br>"
            f"Files skipped: <b>{prog.skipped:,}</b><br>"
            f"Duplicates found: <b>{prog.duplicates:,}</b><br>"
            f"Errors: <b>{prog.errors:,}</b><br>"
            f"Time elapsed: <b>{_fmt_elapsed(prog.elapsed_seconds)}</b>"
        )
        QMessageBox.information(self, "Organize Complete", msg)

    # ------------------------------------------------------------------
    # Status bar helpers (duck-typed, no import of MainWindow)
    # ------------------------------------------------------------------

    def _update_main_window(
        self,
        state: str | None = None,
        file_count: int | None = None,
    ) -> None:
        w = self.window()
        if state is not None and hasattr(w, "set_session_state"):
            w.set_session_state(state)
        if file_count is not None and hasattr(w, "set_file_count"):
            w.set_file_count(file_count)

    def _start_elapsed(self) -> None:
        w = self.window()
        if hasattr(w, "start_elapsed_timer"):
            w.start_elapsed_timer()

    def _stop_elapsed(self) -> None:
        w = self.window()
        if hasattr(w, "stop_elapsed_timer"):
            w.stop_elapsed_timer()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _fmt_elapsed(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"
