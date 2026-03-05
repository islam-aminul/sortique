"""Dry-run results dialog — rich preview before committing to organise."""

from __future__ import annotations

import csv
import json
import os
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QComboBox,
)

from sortique.constants import PairPolicy

if TYPE_CHECKING:
    from sortique.service.dry_run import DryRunSummary


# ---------------------------------------------------------------------------
# Helper: inferred-date file list sub-dialog
# ---------------------------------------------------------------------------

class _InferredDatesDialog(QDialog):
    """Simple list dialog showing files whose dates were inferred."""

    def __init__(self, paths: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Files with Inferred Dates")
        self.setMinimumSize(560, 340)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        lbl = QLabel(
            f"{len(paths):,} file(s) had no EXIF date and were assigned a date "
            "based on nearby files in the same directory."
        )
        lbl.setWordWrap(True)
        layout.addWidget(lbl)

        lst = QListWidget()
        lst.setAlternatingRowColors(True)
        for p in paths:
            lst.addItem(p)
        layout.addWidget(lst)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn_box.rejected.connect(self.reject)
        btn_box.accepted.connect(self.accept)
        layout.addWidget(btn_box)


# ---------------------------------------------------------------------------
# Main dialog
# ---------------------------------------------------------------------------

_PAIR_POLICY_OPTIONS: list[tuple[str, PairPolicy]] = [
    ("Keep both files",  PairPolicy.KEEP_BOTH),
    ("Keep RAW only",    PairPolicy.KEEP_RAW),
    ("Keep JPEG only",   PairPolicy.KEEP_JPEG),
]


class DryRunDialog(QDialog):
    """Modal dialog showing dry-run results for user review before committing."""

    def __init__(self, summary: DryRunSummary, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Preview Results")
        self.setMinimumSize(700, 500)
        self.resize(700, 560)
        self.setModal(True)

        self._summary = summary
        self._pair_policy: PairPolicy = PairPolicy.KEEP_BOTH

        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(0)
        root.setContentsMargins(0, 0, 0, 0)

        # Fixed header
        root.addWidget(self._make_header())

        # Scrollable body
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(16, 12, 16, 12)
        body_layout.setSpacing(12)

        body_layout.addWidget(self._make_stats_section())
        body_layout.addWidget(self._make_category_table())

        warnings_widget = self._make_warnings_section()
        if warnings_widget is not None:
            body_layout.addWidget(warnings_widget)

        pair_widget = self._make_pair_policy_section()
        if pair_widget is not None:
            body_layout.addWidget(pair_widget)

        inferred_widget = self._make_inferred_dates_section()
        if inferred_widget is not None:
            body_layout.addWidget(inferred_widget)

        body_layout.addStretch()
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        # Fixed button bar
        root.addWidget(_h_line())
        root.addWidget(self._make_button_bar())

    # ------------------------------------------------------------------
    # Section builders
    # ------------------------------------------------------------------

    def _make_header(self) -> QWidget:
        w = QWidget()
        w.setObjectName("dlgHeader")
        layout = QVBoxLayout(w)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(2)

        title = QLabel("Preview Results")
        font = title.font()
        font.setPointSize(14)
        font.setBold(True)
        title.setFont(font)

        subtitle = QLabel(
            f"{self._summary.total_files:,} files analysed"
            + (
                f"  ·  {self._summary.files_to_copy:,} to copy"
                f"  ·  {self._summary.files_to_skip:,} to skip"
                if self._summary.total_files > 0
                else ""
            )
        )
        subtitle.setStyleSheet("color: #888;")

        layout.addWidget(title)
        layout.addWidget(subtitle)
        return w

    def _make_stats_section(self) -> QGroupBox:
        group = QGroupBox("Summary")
        form = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(6)

        s = self._summary

        form.addRow("Files to organise:", QLabel(f"{s.files_to_copy:,}"))

        dup_text = f"{s.duplicates_found:,}"
        if s.duplicates_found > 0 and s.estimated_space_bytes > 0 and s.files_to_copy > 0:
            avg = s.estimated_space_bytes / s.files_to_copy
            saved = int(s.duplicates_found * avg)
            dup_text += f"  (≈{_fmt_bytes(saved)} saved)"
        form.addRow("Duplicates found:", QLabel(dup_text))

        form.addRow("Files to skip:", QLabel(f"{s.files_to_skip:,}"))
        form.addRow(
            "Estimated space needed:",
            QLabel(_fmt_bytes(s.estimated_space_bytes)),
        )

        if s.space_check is not None:
            avail_lbl = QLabel(_fmt_bytes(s.space_check.available_bytes))
            if s.space_check.passes:
                avail_lbl.setStyleSheet("color: #4caf50; font-weight: bold;")
            else:
                short = _fmt_bytes(s.space_check.shortfall_bytes)
                avail_lbl.setText(
                    f"{_fmt_bytes(s.space_check.available_bytes)}  "
                    f"(insufficient — {short} short)"
                )
                avail_lbl.setStyleSheet("color: #f44336; font-weight: bold;")
            form.addRow("Available space:", avail_lbl)

        return group

    def _make_category_table(self) -> QGroupBox:
        s = self._summary
        group = QGroupBox("Category Breakdown")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(8, 8, 8, 8)

        if not s.category_breakdown:
            layout.addWidget(QLabel("No categories to display."))
            return group

        # Proportional size estimate per category.
        avg_bytes = (
            s.estimated_space_bytes / s.files_to_copy
            if s.files_to_copy > 0
            else 0.0
        )

        rows = sorted(
            s.category_breakdown.items(), key=lambda kv: -kv[1]
        )

        table = QTableWidget(len(rows), 3)
        table.setHorizontalHeaderLabels(["Category", "File Count", "Est. Size"])
        table.horizontalHeader().setStretchLastSection(True)
        table.verticalHeader().setVisible(False)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)

        for row_idx, (cat, count) in enumerate(rows):
            cat_item = QTableWidgetItem(cat.capitalize() if cat else "—")
            count_item = QTableWidgetItem(f"{count:,}")
            count_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            size_item = QTableWidgetItem(_fmt_bytes(int(count * avg_bytes)))
            size_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            table.setItem(row_idx, 0, cat_item)
            table.setItem(row_idx, 1, count_item)
            table.setItem(row_idx, 2, size_item)

        table.resizeColumnsToContents()
        # Give the table a sensible fixed height (max ~8 rows visible).
        row_h = table.rowHeight(0) if rows else 24
        header_h = table.horizontalHeader().height()
        table.setFixedHeight(min(len(rows) * row_h + header_h + 4, 8 * row_h + header_h + 4))

        layout.addWidget(table)
        return group

    def _make_warnings_section(self) -> QWidget | None:
        if not self._summary.warnings:
            return None

        group = QGroupBox("Warnings")
        layout = QVBoxLayout(group)
        layout.setSpacing(4)

        # Yellow-tinted background for the inner container.
        inner = QWidget()
        inner.setStyleSheet(
            "background-color: #3d3200; border-radius: 4px; padding: 4px;"
        )
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(8, 6, 8, 6)
        inner_layout.setSpacing(4)

        for msg in self._summary.warnings:
            lbl = QLabel(f"⚠  {msg}")
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color: #ffd54f; background: transparent;")
            inner_layout.addWidget(lbl)

        layout.addWidget(inner)
        return group

    def _make_pair_policy_section(self) -> QWidget | None:
        pairs = self._summary.raw_jpeg_pairs
        if not pairs:
            return None

        group = QGroupBox("RAW+JPEG Pair Policy")
        layout = QHBoxLayout(group)
        layout.setSpacing(12)

        lbl = QLabel(f"Found {len(pairs):,} RAW+JPEG pair(s):")
        lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(lbl)

        combo = QComboBox()
        for display, _ in _PAIR_POLICY_OPTIONS:
            combo.addItem(display)
        combo.currentIndexChanged.connect(self._on_pair_policy_changed)
        layout.addWidget(combo)

        self._pair_combo = combo
        return group

    def _make_inferred_dates_section(self) -> QWidget | None:
        paths = self._summary.inferred_date_files
        if not paths:
            return None

        group = QGroupBox("Inferred Dates")
        layout = QHBoxLayout(group)
        layout.setSpacing(12)

        lbl = QLabel(
            f"{len(paths):,} file(s) have dates inferred from nearby files."
        )
        lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(lbl)

        btn = QPushButton("View Details")
        btn.setFixedWidth(100)
        btn.clicked.connect(lambda: self._show_inferred_details(paths))
        layout.addWidget(btn)

        return group

    def _make_button_bar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 8, 16, 12)
        layout.setSpacing(8)

        export_btn = QPushButton("Export Report…")
        export_btn.clicked.connect(self._export_report)
        layout.addWidget(export_btn)

        layout.addStretch()

        btn_box = QDialogButtonBox()
        self._proceed_btn = btn_box.addButton(
            "Proceed", QDialogButtonBox.ButtonRole.AcceptRole
        )
        btn_box.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)

        # Block proceed if disk space check definitively fails.
        sc = self._summary.space_check
        if sc is not None and not sc.passes:
            self._proceed_btn.setEnabled(False)
            self._proceed_btn.setToolTip("Insufficient disk space — cannot proceed.")

        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        return bar

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_pair_policy_changed(self, index: int) -> None:
        self._pair_policy = _PAIR_POLICY_OPTIONS[index][1]

    def _show_inferred_details(self, paths: list[str]) -> None:
        dlg = _InferredDatesDialog(paths, parent=self)
        dlg.exec()

    def _export_report(self) -> None:
        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Preview Report",
            os.path.expanduser("~/sortique_preview_report"),
            "CSV Files (*.csv);;JSON Files (*.json)",
        )
        if not path:
            return

        fmt = "json" if selected_filter.startswith("JSON") or path.endswith(".json") else "csv"

        try:
            if fmt == "json":
                self._write_json_report(path)
            else:
                self._write_csv_report(path)
        except OSError as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Export Failed", str(exc))

    # ------------------------------------------------------------------
    # Export helpers
    # ------------------------------------------------------------------

    def _build_report_data(self) -> dict:
        s = self._summary
        return {
            "summary": {
                "total_files": s.total_files,
                "files_to_copy": s.files_to_copy,
                "files_to_skip": s.files_to_skip,
                "duplicates_found": s.duplicates_found,
                "estimated_space_bytes": s.estimated_space_bytes,
                "available_space_bytes": (
                    s.space_check.available_bytes if s.space_check else None
                ),
                "space_ok": (
                    s.space_check.passes if s.space_check else None
                ),
            },
            "category_breakdown": s.category_breakdown,
            "skip_reasons": s.skip_reasons,
            "warnings": s.warnings,
            "raw_jpeg_pairs": len(s.raw_jpeg_pairs),
            "inferred_date_files": len(s.inferred_date_files),
            "cloud_stubs": len(s.cloud_stubs),
        }

    def _write_json_report(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._build_report_data(), f, indent=2)

    def _write_csv_report(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        s = self._summary
        sc = s.space_check

        rows: list[tuple[str, str, str]] = [
            # --- summary ---
            ("Summary", "total_files",          str(s.total_files)),
            ("Summary", "files_to_copy",         str(s.files_to_copy)),
            ("Summary", "files_to_skip",         str(s.files_to_skip)),
            ("Summary", "duplicates_found",      str(s.duplicates_found)),
            ("Summary", "estimated_space_bytes", str(s.estimated_space_bytes)),
        ]
        if sc is not None:
            rows += [
                ("Summary", "available_space_bytes", str(sc.available_bytes)),
                ("Summary", "space_ok",              str(sc.passes)),
            ]
        # --- categories ---
        for cat, count in sorted(s.category_breakdown.items(), key=lambda kv: -kv[1]):
            rows.append(("Category Breakdown", cat, str(count)))
        # --- skip reasons ---
        for reason, count in sorted(s.skip_reasons.items(), key=lambda kv: -kv[1]):
            rows.append(("Skip Reasons", reason, str(count)))
        # --- warnings ---
        for i, w in enumerate(s.warnings, 1):
            rows.append(("Warnings", str(i), w))

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["section", "key", "value"])
            writer.writerows(rows)

    # ------------------------------------------------------------------
    # Public property
    # ------------------------------------------------------------------

    @property
    def pair_policy(self) -> PairPolicy:
        """The pair policy selected by the user (default: KEEP_BOTH)."""
        return self._pair_policy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _h_line() -> QWidget:
    line = QWidget()
    line.setFixedHeight(1)
    line.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    line.setStyleSheet("background: #3a3a3a;")
    return line
