"""Dry-run simulation — preview the pipeline without writing any files."""

from __future__ import annotations

import csv
import io
import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from sortique.constants import DateSource, FileStatus
from sortique.data.file_system import FileSystemHelper

if TYPE_CHECKING:
    from sortique.data.database import Database
    from sortique.data.models import FileRecord
    from sortique.engine.pair_detector import FilePair, PairDetector
    from sortique.service.pipeline import Pipeline
    from sortique.service.space_checker import SpaceCheckResult, SpaceChecker


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class DryRunSummary:
    """Complete summary of a dry-run simulation."""

    total_files: int = 0
    files_to_copy: int = 0
    files_to_skip: int = 0
    duplicates_found: int = 0
    estimated_space_bytes: int = 0
    space_check: SpaceCheckResult | None = None
    category_breakdown: dict[str, int] = field(default_factory=dict)
    skip_reasons: dict[str, int] = field(default_factory=dict)
    inferred_date_files: list[str] = field(default_factory=list)
    raw_jpeg_pairs: list[FilePair] = field(default_factory=list)
    cloud_stubs: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class DryRunManager:
    """Runs the full pipeline in simulation mode and produces a preview report."""

    def __init__(
        self,
        pipeline: Pipeline,
        space_checker: SpaceChecker,
        pair_detector: PairDetector,
        db: Database,
    ) -> None:
        self.pipeline = pipeline
        self.space_checker = space_checker
        self.pair_detector = pair_detector
        self.db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        file_records: list[FileRecord],
        destination_dir: str,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> DryRunSummary:
        """Execute dry-run: process all files through the pipeline (no I/O).

        Steps:

        1. Run each file through the pipeline (``dry_run=True`` skips copying).
        2. Collect results: categories, duplicates, skips, dates.
        3. Detect RAW+JPEG pairs from processed records.
        4. Calculate total estimated space needed.
        5. Run space check.
        6. Compile summary with all stats and warnings.
        """
        summary = DryRunSummary(total_files=len(file_records))
        category_counts: dict[str, int] = defaultdict(int)
        skip_counts: dict[str, int] = defaultdict(int)
        estimated_bytes = 0

        for idx, record in enumerate(file_records):
            result = self.pipeline.process_file(record)

            if result.final_status == FileStatus.COMPLETED:
                summary.files_to_copy += 1
                estimated_bytes += record.file_size

                if record.category:
                    category_counts[record.category] += 1

            elif result.final_status == FileStatus.SKIPPED:
                summary.files_to_skip += 1

                reason = result.skip_reason or "unknown"
                skip_counts[reason] += 1

                if reason == "exact duplicate":
                    summary.duplicates_found += 1

            elif result.final_status == FileStatus.ERROR:
                summary.files_to_skip += 1
                skip_counts["error"] += 1

            # Track inferred dates.
            if record.date_source == DateSource.INFERRED:
                summary.inferred_date_files.append(record.source_path)

            # Detect cloud stubs.
            is_stub, service = FileSystemHelper.is_cloud_stub(record.source_path)
            if is_stub:
                summary.cloud_stubs.append((record.source_path, service))

            if progress_callback is not None:
                progress_callback(idx + 1, len(file_records))

        summary.estimated_space_bytes = estimated_bytes
        summary.category_breakdown = dict(category_counts)
        summary.skip_reasons = dict(skip_counts)

        # Detect RAW+JPEG pairs.
        summary.raw_jpeg_pairs = self.pair_detector.detect_pairs(file_records)

        # Space check.
        summary.space_check = self.space_checker.check(
            estimated_bytes, destination_dir,
        )

        # Compile warnings.
        summary.warnings = self._compile_warnings(file_records, summary)

        return summary

    def export_detailed_report(
        self,
        session_id: str,
        output_path: str,
        format: str = "csv",
    ) -> str:
        """Export a detailed file-by-file report.

        CSV columns: source_path, destination_path, action, category,
        file_type, date_value, date_source, file_size, warnings.

        JSON format: array of objects with the same fields.

        Returns the path to the exported file.
        """
        records = self.db.get_file_records(session_id)
        rows = self._build_report_rows(records)

        if format == "json":
            return self._write_json(rows, output_path)
        return self._write_csv(rows, output_path)

    # ------------------------------------------------------------------
    # Report helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_report_rows(records: list[FileRecord]) -> list[dict]:
        """Convert FileRecords into flat report rows."""
        rows: list[dict] = []
        for rec in records:
            if rec.status == FileStatus.COMPLETED:
                action = "copy"
            elif rec.status == FileStatus.SKIPPED and rec.skip_reason == "exact duplicate":
                action = "duplicate"
            elif rec.status == FileStatus.SKIPPED:
                action = "skip"
            else:
                action = "error"

            row_warnings: list[str] = []
            if rec.date_source == DateSource.INFERRED:
                row_warnings.append("inferred date")
            is_stub, service = FileSystemHelper.is_cloud_stub(rec.source_path)
            if is_stub:
                row_warnings.append(f"cloud stub ({service})")

            rows.append({
                "source_path": rec.source_path,
                "destination_path": rec.destination_path or "",
                "action": action,
                "category": rec.category,
                "file_type": rec.file_type.value,
                "date_value": rec.date_value.isoformat() if rec.date_value else "",
                "date_source": rec.date_source.value,
                "file_size": rec.file_size,
                "warnings": "; ".join(row_warnings),
            })
        return rows

    @staticmethod
    def _write_csv(rows: list[dict], output_path: str) -> str:
        """Write rows as CSV to *output_path*."""
        fieldnames = [
            "source_path", "destination_path", "action", "category",
            "file_type", "date_value", "date_source", "file_size", "warnings",
        ]
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        return output_path

    @staticmethod
    def _write_json(rows: list[dict], output_path: str) -> str:
        """Write rows as JSON to *output_path*."""
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2)
        return output_path

    # ------------------------------------------------------------------
    # Warning compilation
    # ------------------------------------------------------------------

    @staticmethod
    def _compile_warnings(
        file_records: list[FileRecord],
        summary: DryRunSummary,
    ) -> list[str]:
        """Generate warning messages based on dry-run results."""
        warnings: list[str] = []

        # Space check failure.
        if summary.space_check is not None and not summary.space_check.passes:
            shortfall_mb = summary.space_check.shortfall_bytes / (1024 * 1024)
            warnings.append(
                f"Insufficient disk space: {shortfall_mb:.1f} MB shortfall"
            )

        # High duplicate ratio (>50%).
        if summary.total_files > 0:
            dup_ratio = summary.duplicates_found / summary.total_files
            if dup_ratio > 0.5:
                pct = dup_ratio * 100
                warnings.append(
                    f"High duplicate ratio: {pct:.0f}% of files are duplicates"
                )

        # Many inferred dates (>25% of files to copy).
        if summary.files_to_copy > 0:
            inferred_ratio = len(summary.inferred_date_files) / summary.files_to_copy
            if inferred_ratio > 0.25:
                warnings.append(
                    f"{len(summary.inferred_date_files)} files have inferred dates"
                    f" ({inferred_ratio * 100:.0f}% of files to copy)"
                )

        # Cloud stubs.
        if summary.cloud_stubs:
            warnings.append(
                f"{len(summary.cloud_stubs)} cloud stub(s) detected"
                " — files may not be downloaded locally"
            )

        # Unresolved pair conflicts (RAW+JPEG pairs with no pair_policy set).
        unresolved = 0
        path_lookup = {rec.source_path: rec for rec in file_records}
        for pair in summary.raw_jpeg_pairs:
            raw_rec = path_lookup.get(pair.raw_path)
            if raw_rec is not None and raw_rec.pair_policy is None:
                unresolved += 1
        if unresolved:
            warnings.append(
                f"{unresolved} RAW+JPEG pair(s) have no pair policy set"
            )

        return warnings
