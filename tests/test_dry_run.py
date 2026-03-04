"""Tests for DryRunManager and DryRunSummary."""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from sortique.constants import DateSource, FileStatus, FileType, PairPolicy
from sortique.data.models import FileRecord
from sortique.engine.pair_detector import FilePair
from sortique.service.dry_run import DryRunManager, DryRunSummary
from sortique.service.pipeline import PipelineResult
from sortique.service.space_checker import SpaceCheckResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_record(
    source_path: str = "/src/photo.jpg",
    *,
    session_id: str = "sess-1",
    source_dir: str = "/src",
    file_size: int = 1000,
    file_type: FileType = FileType.IMAGE,
    category: str = "Photos",
    status: FileStatus = FileStatus.PENDING,
    date_source: DateSource = DateSource.METADATA,
    date_value: datetime | None = None,
    destination_path: str | None = None,
    skip_reason: str | None = None,
    pair_policy: PairPolicy | None = None,
) -> FileRecord:
    return FileRecord(
        session_id=session_id,
        source_path=source_path,
        source_dir=source_dir,
        file_size=file_size,
        file_type=file_type,
        category=category,
        status=status,
        date_source=date_source,
        date_value=date_value,
        destination_path=destination_path,
        skip_reason=skip_reason,
        pair_policy=pair_policy,
    )


def _passing_space_check() -> SpaceCheckResult:
    return SpaceCheckResult(
        required_bytes=1000,
        available_bytes=999999,
        passes=True,
        shortfall_bytes=0,
    )


def _failing_space_check(shortfall: int = 5_000_000) -> SpaceCheckResult:
    return SpaceCheckResult(
        required_bytes=10_000_000,
        available_bytes=10_000_000 - shortfall,
        passes=False,
        shortfall_bytes=shortfall,
    )


def _build_manager(
    process_results: list[PipelineResult] | None = None,
    *,
    space_check: SpaceCheckResult | None = None,
    pairs: list[FilePair] | None = None,
    db_records: list[FileRecord] | None = None,
) -> DryRunManager:
    """Create a DryRunManager with mocked dependencies."""
    pipeline = MagicMock()
    if process_results is not None:
        pipeline.process_file.side_effect = process_results

    space_checker = MagicMock()
    space_checker.check.return_value = space_check or _passing_space_check()

    pair_detector = MagicMock()
    pair_detector.detect_pairs.return_value = pairs or []

    db = MagicMock()
    db.get_file_records.return_value = db_records or []

    return DryRunManager(pipeline, space_checker, pair_detector, db)


# ---------------------------------------------------------------------------
# DryRunSummary dataclass
# ---------------------------------------------------------------------------

class TestDryRunSummary:
    def test_defaults(self):
        s = DryRunSummary()
        assert s.total_files == 0
        assert s.files_to_copy == 0
        assert s.files_to_skip == 0
        assert s.duplicates_found == 0
        assert s.estimated_space_bytes == 0
        assert s.space_check is None
        assert s.category_breakdown == {}
        assert s.skip_reasons == {}
        assert s.inferred_date_files == []
        assert s.raw_jpeg_pairs == []
        assert s.cloud_stubs == []
        assert s.warnings == []

    def test_custom_values(self):
        sc = _passing_space_check()
        pair = FilePair(raw_path="/r.cr2", jpeg_path="/r.jpg", stem="r")
        s = DryRunSummary(
            total_files=10,
            files_to_copy=7,
            files_to_skip=3,
            duplicates_found=2,
            estimated_space_bytes=7000,
            space_check=sc,
            category_breakdown={"Photos": 5, "Videos": 2},
            skip_reasons={"exact duplicate": 2, "hidden or system file": 1},
            inferred_date_files=["/src/a.jpg"],
            raw_jpeg_pairs=[pair],
            cloud_stubs=[("/src/x.icloud", "icloud")],
            warnings=["test warning"],
        )
        assert s.total_files == 10
        assert s.files_to_copy == 7
        assert s.duplicates_found == 2
        assert len(s.raw_jpeg_pairs) == 1
        assert s.warnings == ["test warning"]


# ---------------------------------------------------------------------------
# Dry-run produces correct summary stats
# ---------------------------------------------------------------------------

class TestDryRunSummaryStats:
    def test_basic_counts(self):
        """Completed, skipped, and error files are counted correctly."""
        records = [
            _make_record("/src/a.jpg", file_size=1000),
            _make_record("/src/b.jpg", file_size=2000),
            _make_record("/src/c.jpg", file_size=3000),
            _make_record("/src/d.jpg", file_size=500),
        ]

        results = [
            PipelineResult(file_id="1", final_status=FileStatus.COMPLETED, stages_completed=13),
            PipelineResult(file_id="2", final_status=FileStatus.COMPLETED, stages_completed=13),
            PipelineResult(file_id="3", final_status=FileStatus.SKIPPED, skip_reason="hidden or system file", stages_completed=3),
            PipelineResult(file_id="4", final_status=FileStatus.ERROR, error_message="boom", stages_completed=4),
        ]

        # Pipeline sets category on record during processing; simulate that.
        def process_side_effect(record):
            idx = records.index(record)
            if results[idx].final_status == FileStatus.COMPLETED:
                record.category = "Photos"
            return results[idx]

        mgr = _build_manager()
        mgr.pipeline.process_file.side_effect = process_side_effect

        summary = mgr.run(records, "/dst")

        assert summary.total_files == 4
        assert summary.files_to_copy == 2
        assert summary.files_to_skip == 2
        assert summary.estimated_space_bytes == 3000  # 1000 + 2000
        assert summary.duplicates_found == 0

    def test_duplicate_counting(self):
        records = [
            _make_record("/src/a.jpg", file_size=1000),
            _make_record("/src/b.jpg", file_size=2000),
            _make_record("/src/c.jpg", file_size=3000),
        ]

        results = [
            PipelineResult(file_id="1", final_status=FileStatus.COMPLETED, stages_completed=13),
            PipelineResult(file_id="2", final_status=FileStatus.SKIPPED, skip_reason="exact duplicate", stages_completed=6),
            PipelineResult(file_id="3", final_status=FileStatus.SKIPPED, skip_reason="exact duplicate", stages_completed=6),
        ]

        def process_side_effect(record):
            idx = records.index(record)
            if results[idx].final_status == FileStatus.COMPLETED:
                record.category = "Photos"
            return results[idx]

        mgr = _build_manager()
        mgr.pipeline.process_file.side_effect = process_side_effect

        summary = mgr.run(records, "/dst")

        assert summary.duplicates_found == 2
        assert summary.files_to_skip == 2
        assert summary.skip_reasons["exact duplicate"] == 2

    def test_category_breakdown(self):
        records = [
            _make_record("/src/a.jpg", file_size=100),
            _make_record("/src/b.jpg", file_size=200),
            _make_record("/src/c.mp4", file_size=300),
        ]

        results = [
            PipelineResult(file_id="1", final_status=FileStatus.COMPLETED, stages_completed=13),
            PipelineResult(file_id="2", final_status=FileStatus.COMPLETED, stages_completed=13),
            PipelineResult(file_id="3", final_status=FileStatus.COMPLETED, stages_completed=13),
        ]

        def process_side_effect(record):
            idx = records.index(record)
            if idx < 2:
                record.category = "Photos"
            else:
                record.category = "Videos"
            return results[idx]

        mgr = _build_manager()
        mgr.pipeline.process_file.side_effect = process_side_effect

        summary = mgr.run(records, "/dst")

        assert summary.category_breakdown == {"Photos": 2, "Videos": 1}

    def test_inferred_date_tracking(self):
        records = [
            _make_record("/src/a.jpg"),
            _make_record("/src/b.jpg"),
        ]

        results = [
            PipelineResult(file_id="1", final_status=FileStatus.COMPLETED, stages_completed=13),
            PipelineResult(file_id="2", final_status=FileStatus.COMPLETED, stages_completed=13),
        ]

        def process_side_effect(record):
            idx = records.index(record)
            record.category = "Photos"
            # Simulate pipeline setting date_source.
            if idx == 1:
                record.date_source = DateSource.INFERRED
            return results[idx]

        mgr = _build_manager()
        mgr.pipeline.process_file.side_effect = process_side_effect

        summary = mgr.run(records, "/dst")

        assert summary.inferred_date_files == ["/src/b.jpg"]

    def test_skip_reasons_aggregation(self):
        records = [
            _make_record(f"/src/f{i}.jpg") for i in range(4)
        ]

        results = [
            PipelineResult(file_id="1", final_status=FileStatus.SKIPPED, skip_reason="hidden or system file", stages_completed=3),
            PipelineResult(file_id="2", final_status=FileStatus.SKIPPED, skip_reason="hidden or system file", stages_completed=3),
            PipelineResult(file_id="3", final_status=FileStatus.SKIPPED, skip_reason="exact duplicate", stages_completed=6),
            PipelineResult(file_id="4", final_status=FileStatus.SKIPPED, skip_reason="unknown file type", stages_completed=5),
        ]

        mgr = _build_manager(results)
        summary = mgr.run(records, "/dst")

        assert summary.skip_reasons == {
            "hidden or system file": 2,
            "exact duplicate": 1,
            "unknown file type": 1,
        }

    def test_space_check_called(self):
        records = [_make_record("/src/a.jpg", file_size=5000)]
        results = [
            PipelineResult(file_id="1", final_status=FileStatus.COMPLETED, stages_completed=13),
        ]

        def process_side_effect(record):
            record.category = "Photos"
            return results[0]

        sc = _passing_space_check()
        mgr = _build_manager(space_check=sc)
        mgr.pipeline.process_file.side_effect = process_side_effect

        summary = mgr.run(records, "/dst")

        mgr.space_checker.check.assert_called_once_with(5000, "/dst")
        assert summary.space_check is sc

    def test_pair_detection_called(self):
        records = [
            _make_record("/src/IMG_001.cr2", source_dir="/src"),
            _make_record("/src/IMG_001.jpg", source_dir="/src"),
        ]
        results = [
            PipelineResult(file_id="1", final_status=FileStatus.COMPLETED, stages_completed=13),
            PipelineResult(file_id="2", final_status=FileStatus.COMPLETED, stages_completed=13),
        ]

        def process_side_effect(record):
            record.category = "Photos"
            return results.pop(0)

        pair = FilePair(raw_path="/src/IMG_001.cr2", jpeg_path="/src/IMG_001.jpg", stem="IMG_001")
        mgr = _build_manager(pairs=[pair])
        mgr.pipeline.process_file.side_effect = process_side_effect

        summary = mgr.run(records, "/dst")

        mgr.pair_detector.detect_pairs.assert_called_once_with(records)
        assert summary.raw_jpeg_pairs == [pair]

    def test_progress_callback_called(self):
        records = [
            _make_record(f"/src/f{i}.jpg") for i in range(3)
        ]
        results = [
            PipelineResult(file_id=str(i), final_status=FileStatus.COMPLETED, stages_completed=13)
            for i in range(3)
        ]

        def process_side_effect(record):
            record.category = "Photos"
            return results.pop(0)

        mgr = _build_manager()
        mgr.pipeline.process_file.side_effect = process_side_effect

        calls: list[tuple[int, int]] = []
        summary = mgr.run(records, "/dst", progress_callback=lambda a, b: calls.append((a, b)))

        assert calls == [(1, 3), (2, 3), (3, 3)]

    def test_empty_records(self):
        mgr = _build_manager(process_results=[])
        summary = mgr.run([], "/dst")

        assert summary.total_files == 0
        assert summary.files_to_copy == 0
        assert summary.files_to_skip == 0


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

class TestCsvExport:
    def test_csv_columns(self, tmp_path):
        rec = _make_record(
            "/src/photo.jpg",
            file_size=1234,
            status=FileStatus.COMPLETED,
            category="Photos",
            destination_path="/dst/Photos/photo.jpg",
            date_source=DateSource.METADATA,
        )
        rec.date_value = datetime(2024, 6, 15, tzinfo=timezone.utc)

        mgr = _build_manager(db_records=[rec])
        out = str(tmp_path / "report.csv")
        result_path = mgr.export_detailed_report("sess-1", out, format="csv")

        assert result_path == out
        assert os.path.exists(out)

        with open(out, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 1
        row = rows[0]
        assert row["source_path"] == "/src/photo.jpg"
        assert row["destination_path"] == "/dst/Photos/photo.jpg"
        assert row["action"] == "copy"
        assert row["category"] == "Photos"
        assert row["file_type"] == "image"
        assert row["date_source"] == "metadata"
        assert row["file_size"] == "1234"

    def test_csv_action_types(self, tmp_path):
        records = [
            _make_record("/src/a.jpg", status=FileStatus.COMPLETED),
            _make_record("/src/b.jpg", status=FileStatus.SKIPPED, skip_reason="hidden or system file"),
            _make_record("/src/c.jpg", status=FileStatus.SKIPPED, skip_reason="exact duplicate"),
            _make_record("/src/d.jpg", status=FileStatus.ERROR),
        ]

        mgr = _build_manager(db_records=records)
        out = str(tmp_path / "report.csv")
        mgr.export_detailed_report("sess-1", out, format="csv")

        with open(out, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        actions = [r["action"] for r in rows]
        assert actions == ["copy", "skip", "duplicate", "error"]

    def test_csv_inferred_date_warning(self, tmp_path):
        rec = _make_record("/src/x.jpg", status=FileStatus.COMPLETED, date_source=DateSource.INFERRED)
        mgr = _build_manager(db_records=[rec])
        out = str(tmp_path / "report.csv")
        mgr.export_detailed_report("sess-1", out, format="csv")

        with open(out, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        assert "inferred date" in rows[0]["warnings"]

    def test_csv_empty_records(self, tmp_path):
        mgr = _build_manager(db_records=[])
        out = str(tmp_path / "report.csv")
        mgr.export_detailed_report("sess-1", out, format="csv")

        with open(out, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            assert len(rows) == 0
            # Header should still exist.
            f.seek(0)
            header = f.readline().strip()
            assert "source_path" in header

    def test_csv_multiple_records(self, tmp_path):
        records = [
            _make_record(f"/src/f{i}.jpg", status=FileStatus.COMPLETED, file_size=100 * (i + 1))
            for i in range(5)
        ]
        mgr = _build_manager(db_records=records)
        out = str(tmp_path / "report.csv")
        mgr.export_detailed_report("sess-1", out, format="csv")

        with open(out, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        assert len(rows) == 5


# ---------------------------------------------------------------------------
# JSON export
# ---------------------------------------------------------------------------

class TestJsonExport:
    def test_json_structure(self, tmp_path):
        rec = _make_record(
            "/src/photo.jpg",
            file_size=1234,
            status=FileStatus.COMPLETED,
            category="Photos",
            destination_path="/dst/Photos/photo.jpg",
            date_source=DateSource.METADATA,
        )
        rec.date_value = datetime(2024, 6, 15, tzinfo=timezone.utc)

        mgr = _build_manager(db_records=[rec])
        out = str(tmp_path / "report.json")
        result_path = mgr.export_detailed_report("sess-1", out, format="json")

        assert result_path == out
        assert os.path.exists(out)

        with open(out, encoding="utf-8") as f:
            data = json.load(f)

        assert isinstance(data, list)
        assert len(data) == 1
        row = data[0]
        assert row["source_path"] == "/src/photo.jpg"
        assert row["destination_path"] == "/dst/Photos/photo.jpg"
        assert row["action"] == "copy"
        assert row["category"] == "Photos"
        assert row["file_type"] == "image"
        assert row["date_source"] == "metadata"
        assert row["file_size"] == 1234  # int, not string

    def test_json_action_types(self, tmp_path):
        records = [
            _make_record("/src/a.jpg", status=FileStatus.COMPLETED),
            _make_record("/src/b.jpg", status=FileStatus.SKIPPED, skip_reason="hidden or system file"),
            _make_record("/src/c.jpg", status=FileStatus.SKIPPED, skip_reason="exact duplicate"),
            _make_record("/src/d.jpg", status=FileStatus.ERROR),
        ]

        mgr = _build_manager(db_records=records)
        out = str(tmp_path / "report.json")
        mgr.export_detailed_report("sess-1", out, format="json")

        with open(out, encoding="utf-8") as f:
            data = json.load(f)

        actions = [r["action"] for r in data]
        assert actions == ["copy", "skip", "duplicate", "error"]

    def test_json_empty_records(self, tmp_path):
        mgr = _build_manager(db_records=[])
        out = str(tmp_path / "report.json")
        mgr.export_detailed_report("sess-1", out, format="json")

        with open(out, encoding="utf-8") as f:
            data = json.load(f)

        assert data == []

    def test_json_multiple_records(self, tmp_path):
        records = [
            _make_record(f"/src/f{i}.jpg", status=FileStatus.COMPLETED, file_size=100 * (i + 1))
            for i in range(5)
        ]
        mgr = _build_manager(db_records=records)
        out = str(tmp_path / "report.json")
        mgr.export_detailed_report("sess-1", out, format="json")

        with open(out, encoding="utf-8") as f:
            data = json.load(f)

        assert len(data) == 5

    def test_json_date_value_formatted(self, tmp_path):
        rec = _make_record("/src/x.jpg", status=FileStatus.COMPLETED)
        rec.date_value = datetime(2023, 12, 25, 10, 30, 0, tzinfo=timezone.utc)

        mgr = _build_manager(db_records=[rec])
        out = str(tmp_path / "report.json")
        mgr.export_detailed_report("sess-1", out, format="json")

        with open(out, encoding="utf-8") as f:
            data = json.load(f)

        assert "2023-12-25" in data[0]["date_value"]

    def test_json_no_date_value(self, tmp_path):
        rec = _make_record("/src/x.jpg", status=FileStatus.COMPLETED)
        rec.date_value = None

        mgr = _build_manager(db_records=[rec])
        out = str(tmp_path / "report.json")
        mgr.export_detailed_report("sess-1", out, format="json")

        with open(out, encoding="utf-8") as f:
            data = json.load(f)

        assert data[0]["date_value"] == ""


# ---------------------------------------------------------------------------
# Warning generation
# ---------------------------------------------------------------------------

class TestWarningGeneration:
    def test_space_check_failure_warning(self):
        records = [_make_record("/src/a.jpg", file_size=5000)]
        results = [
            PipelineResult(file_id="1", final_status=FileStatus.COMPLETED, stages_completed=13),
        ]

        def process_side_effect(record):
            record.category = "Photos"
            return results[0]

        mgr = _build_manager(space_check=_failing_space_check(5_000_000))
        mgr.pipeline.process_file.side_effect = process_side_effect

        summary = mgr.run(records, "/dst")

        assert any("Insufficient disk space" in w for w in summary.warnings)
        assert any("4.8 MB" in w for w in summary.warnings)

    def test_high_duplicate_warning(self):
        records = [
            _make_record(f"/src/f{i}.jpg") for i in range(4)
        ]

        # 3 out of 4 are duplicates (75%).
        results = [
            PipelineResult(file_id="1", final_status=FileStatus.COMPLETED, stages_completed=13),
            PipelineResult(file_id="2", final_status=FileStatus.SKIPPED, skip_reason="exact duplicate", stages_completed=6),
            PipelineResult(file_id="3", final_status=FileStatus.SKIPPED, skip_reason="exact duplicate", stages_completed=6),
            PipelineResult(file_id="4", final_status=FileStatus.SKIPPED, skip_reason="exact duplicate", stages_completed=6),
        ]

        def process_side_effect(record):
            idx = records.index(record)
            if results[idx].final_status == FileStatus.COMPLETED:
                record.category = "Photos"
            return results[idx]

        mgr = _build_manager()
        mgr.pipeline.process_file.side_effect = process_side_effect

        summary = mgr.run(records, "/dst")

        assert any("High duplicate ratio" in w for w in summary.warnings)
        assert any("75%" in w for w in summary.warnings)

    def test_no_duplicate_warning_below_threshold(self):
        records = [
            _make_record(f"/src/f{i}.jpg") for i in range(4)
        ]

        # 1 out of 4 duplicates (25%) — below 50% threshold.
        results = [
            PipelineResult(file_id="1", final_status=FileStatus.COMPLETED, stages_completed=13),
            PipelineResult(file_id="2", final_status=FileStatus.COMPLETED, stages_completed=13),
            PipelineResult(file_id="3", final_status=FileStatus.COMPLETED, stages_completed=13),
            PipelineResult(file_id="4", final_status=FileStatus.SKIPPED, skip_reason="exact duplicate", stages_completed=6),
        ]

        def process_side_effect(record):
            idx = records.index(record)
            if results[idx].final_status == FileStatus.COMPLETED:
                record.category = "Photos"
            return results[idx]

        mgr = _build_manager()
        mgr.pipeline.process_file.side_effect = process_side_effect

        summary = mgr.run(records, "/dst")

        assert not any("High duplicate ratio" in w for w in summary.warnings)

    def test_many_inferred_dates_warning(self):
        records = [
            _make_record(f"/src/f{i}.jpg") for i in range(4)
        ]

        results = [
            PipelineResult(file_id=str(i), final_status=FileStatus.COMPLETED, stages_completed=13)
            for i in range(4)
        ]

        def process_side_effect(record):
            idx = records.index(record)
            record.category = "Photos"
            # 2 out of 4 files with inferred dates (50% > 25% threshold).
            if idx >= 2:
                record.date_source = DateSource.INFERRED
            return results[idx]

        mgr = _build_manager()
        mgr.pipeline.process_file.side_effect = process_side_effect

        summary = mgr.run(records, "/dst")

        assert any("inferred dates" in w for w in summary.warnings)

    def test_no_inferred_warning_below_threshold(self):
        records = [
            _make_record(f"/src/f{i}.jpg") for i in range(10)
        ]

        results = [
            PipelineResult(file_id=str(i), final_status=FileStatus.COMPLETED, stages_completed=13)
            for i in range(10)
        ]

        def process_side_effect(record):
            idx = records.index(record)
            record.category = "Photos"
            # Only 1 out of 10 inferred (10% < 25%).
            if idx == 0:
                record.date_source = DateSource.INFERRED
            return results[idx]

        mgr = _build_manager()
        mgr.pipeline.process_file.side_effect = process_side_effect

        summary = mgr.run(records, "/dst")

        assert not any("inferred dates" in w for w in summary.warnings)

    def test_unresolved_pair_warning(self):
        records = [
            _make_record("/src/IMG.cr2", source_dir="/src"),
            _make_record("/src/IMG.jpg", source_dir="/src"),
        ]

        results = [
            PipelineResult(file_id="1", final_status=FileStatus.COMPLETED, stages_completed=13),
            PipelineResult(file_id="2", final_status=FileStatus.COMPLETED, stages_completed=13),
        ]

        def process_side_effect(record):
            record.category = "Photos"
            return results.pop(0)

        pair = FilePair(raw_path="/src/IMG.cr2", jpeg_path="/src/IMG.jpg", stem="IMG")
        mgr = _build_manager(pairs=[pair])
        mgr.pipeline.process_file.side_effect = process_side_effect

        summary = mgr.run(records, "/dst")

        assert any("pair policy" in w for w in summary.warnings)

    def test_no_pair_warning_when_policy_set(self):
        records = [
            _make_record("/src/IMG.cr2", source_dir="/src", pair_policy=PairPolicy.KEEP_BOTH),
            _make_record("/src/IMG.jpg", source_dir="/src"),
        ]

        results = [
            PipelineResult(file_id="1", final_status=FileStatus.COMPLETED, stages_completed=13),
            PipelineResult(file_id="2", final_status=FileStatus.COMPLETED, stages_completed=13),
        ]

        def process_side_effect(record):
            record.category = "Photos"
            return results.pop(0)

        pair = FilePair(raw_path="/src/IMG.cr2", jpeg_path="/src/IMG.jpg", stem="IMG")
        mgr = _build_manager(pairs=[pair])
        mgr.pipeline.process_file.side_effect = process_side_effect

        summary = mgr.run(records, "/dst")

        assert not any("pair policy" in w for w in summary.warnings)

    def test_no_warnings_clean_run(self):
        records = [_make_record("/src/a.jpg", file_size=1000)]
        results = [
            PipelineResult(file_id="1", final_status=FileStatus.COMPLETED, stages_completed=13),
        ]

        def process_side_effect(record):
            record.category = "Photos"
            return results[0]

        mgr = _build_manager()
        mgr.pipeline.process_file.side_effect = process_side_effect

        summary = mgr.run(records, "/dst")

        assert summary.warnings == []

    def test_multiple_warnings_combined(self):
        """Space failure + high duplicates should both appear."""
        records = [
            _make_record(f"/src/f{i}.jpg") for i in range(4)
        ]

        results = [
            PipelineResult(file_id="1", final_status=FileStatus.COMPLETED, stages_completed=13),
            PipelineResult(file_id="2", final_status=FileStatus.SKIPPED, skip_reason="exact duplicate", stages_completed=6),
            PipelineResult(file_id="3", final_status=FileStatus.SKIPPED, skip_reason="exact duplicate", stages_completed=6),
            PipelineResult(file_id="4", final_status=FileStatus.SKIPPED, skip_reason="exact duplicate", stages_completed=6),
        ]

        def process_side_effect(record):
            idx = records.index(record)
            if results[idx].final_status == FileStatus.COMPLETED:
                record.category = "Photos"
            return results[idx]

        mgr = _build_manager(space_check=_failing_space_check())
        mgr.pipeline.process_file.side_effect = process_side_effect

        summary = mgr.run(records, "/dst")

        warning_text = " ".join(summary.warnings)
        assert "Insufficient disk space" in warning_text
        assert "High duplicate ratio" in warning_text
