"""Tests for sortique.engine.burst_detector."""

from __future__ import annotations

import os
from datetime import datetime

import pytest

from sortique.constants import DateSource, ExifStatus
from sortique.data.config_manager import ConfigManager
from sortique.engine.burst_detector import BurstDetector, BurstGroup, _camera_key
from sortique.engine.metadata.date_parser import DateResult
from sortique.engine.metadata.exif_extractor import ExifResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _exif(
    make: str | None = None,
    model: str | None = None,
    exif_data: dict | None = None,
) -> ExifResult:
    result = ExifResult(make=make, model=model)
    # Attach raw EXIF data as an ad-hoc attribute (mirrors what some
    # extractors expose for MakerNote tags like BurstMode).
    result.exif_data = exif_data  # type: ignore[attr-defined]
    return result


def _dr(dt: datetime | None = None) -> DateResult:
    return DateResult(
        date=dt,
        source=DateSource.METADATA if dt else DateSource.NONE,
    )


def _rec(
    path: str,
    make: str | None = None,
    model: str | None = None,
    dt: datetime | None = None,
    exif_data: dict | None = None,
) -> tuple[str, ExifResult, DateResult]:
    """Build a (filepath, ExifResult, DateResult) tuple."""
    return (path, _exif(make, model, exif_data), _dr(dt))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def config(tmp_path):
    return ConfigManager(config_dir=str(tmp_path / "cfg"))


@pytest.fixture()
def detector(config):
    return BurstDetector(config)


# ===========================================================================
# 1.  _camera_key helper
# ===========================================================================

class TestCameraKey:

    def test_make_and_model(self):
        assert _camera_key(_exif(make="Canon", model="EOS R5")) == "Canon - EOS R5"

    def test_redundant_prefix(self):
        """Model starts with make → just use model."""
        assert _camera_key(_exif(make="Apple", model="Apple iPhone 15")) == "Apple iPhone 15"

    def test_make_only(self):
        assert _camera_key(_exif(make="Nikon")) == "Nikon"

    def test_model_only(self):
        assert _camera_key(_exif(model="Pixel 8 Pro")) == "Pixel 8 Pro"

    def test_both_none(self):
        assert _camera_key(_exif()) is None

    def test_none_exif(self):
        assert _camera_key(None) is None


# ===========================================================================
# 2.  EXIF burst detection (_check_exif_burst)
# ===========================================================================

class TestExifBurstDetection:

    def test_burst_mode_nonzero(self, detector):
        exif = _exif(exif_data={"BurstMode": 1})
        assert detector._check_exif_burst("/img.jpg", exif) is True

    def test_burst_mode_zero(self, detector):
        exif = _exif(exif_data={"BurstMode": 0})
        assert detector._check_exif_burst("/img.jpg", exif) is False

    def test_sequence_number_present(self, detector):
        exif = _exif(exif_data={"SequenceNumber": 3})
        assert detector._check_exif_burst("/img.jpg", exif) is True

    def test_no_burst_tags(self, detector):
        exif = _exif(exif_data={"ISO": 400})
        assert detector._check_exif_burst("/img.jpg", exif) is False

    def test_no_exif_data(self, detector):
        exif = _exif()
        assert detector._check_exif_burst("/img.jpg", exif) is False

    def test_none_exif(self, detector):
        assert detector._check_exif_burst("/img.jpg", None) is False

    def test_exif_burst_grouping(self, detector):
        """Files with EXIF burst tags are grouped by camera + timestamp."""
        ts = datetime(2024, 3, 15, 14, 30, 0)
        records = [
            _rec("/img1.jpg", "Canon", "EOS R5", ts, {"BurstMode": 1}),
            _rec("/img2.jpg", "Canon", "EOS R5", ts, {"BurstMode": 1}),
            _rec("/img3.jpg", "Canon", "EOS R5", ts, {"BurstMode": 1}),
        ]
        groups = detector.detect_bursts(records)
        assert len(groups) == 1
        assert len(groups[0].files) == 3
        assert groups[0].camera == "Canon - EOS R5"

    def test_exif_burst_two_files_still_grouped(self, detector):
        """EXIF layer groups even 2 files (threshold is 2 for EXIF)."""
        ts = datetime(2024, 1, 1, 12, 0, 0)
        records = [
            _rec("/a.jpg", "Nikon", "Z8", ts, {"SequenceNumber": 0}),
            _rec("/b.jpg", "Nikon", "Z8", ts, {"SequenceNumber": 1}),
        ]
        groups = detector.detect_bursts(records)
        assert len(groups) == 1
        assert len(groups[0].files) == 2


# ===========================================================================
# 3.  Filename pattern grouping
# ===========================================================================

class TestFilenamePatternGrouping:

    def test_burst_pattern_match(self, detector):
        """Files matching *_BURST* pattern are grouped by shared prefix."""
        ts = datetime(2024, 3, 15, 14, 30, 0)
        records = [
            _rec("/photos/IMG_20240315_143000_BURST001.jpg", dt=ts),
            _rec("/photos/IMG_20240315_143000_BURST002.jpg", dt=ts),
            _rec("/photos/IMG_20240315_143000_BURST003.jpg", dt=ts),
        ]
        groups = detector.detect_bursts(records)
        assert len(groups) == 1
        assert len(groups[0].files) == 3

    def test_bracketed_pattern_match(self, detector):
        """Files matching *_BRACKETED* pattern are grouped."""
        ts = datetime(2024, 6, 1, 9, 0, 0)
        records = [
            _rec("/photos/DSC_001_BRACKETED1.jpg", dt=ts),
            _rec("/photos/DSC_001_BRACKETED2.jpg", dt=ts),
        ]
        groups = detector.detect_bursts(records)
        assert len(groups) == 1
        assert len(groups[0].files) == 2

    def test_different_prefixes_separate_groups(self, detector):
        """Different stem prefixes result in separate burst groups."""
        ts = datetime(2024, 3, 15, 14, 30, 0)
        records = [
            _rec("/photos/IMG_001_BURST001.jpg", dt=ts),
            _rec("/photos/IMG_001_BURST002.jpg", dt=ts),
            _rec("/photos/IMG_002_BURST001.jpg", dt=ts),
            _rec("/photos/IMG_002_BURST002.jpg", dt=ts),
        ]
        groups = detector.detect_bursts(records)
        assert len(groups) == 2
        for g in groups:
            assert len(g.files) == 2

    def test_non_burst_filename_ignored(self, detector):
        """Files that don't match burst patterns are not grouped by layer 2."""
        records = [
            _rec("/photos/IMG_001.jpg", dt=datetime(2024, 1, 1, 12, 0, 0)),
            _rec("/photos/IMG_002.jpg", dt=datetime(2024, 1, 1, 12, 0, 0)),
        ]
        # These don't have burst keywords, so layer 2 produces nothing.
        # Layer 3 needs >=3 files, so no groups.
        groups = detector.detect_bursts(records)
        assert len(groups) == 0

    def test_single_burst_file_not_grouped(self, detector):
        """A single file matching the burst pattern isn't a group."""
        records = [
            _rec("/photos/IMG_001_BURST001.jpg", dt=datetime(2024, 1, 1, 12, 0, 0)),
        ]
        groups = detector.detect_bursts(records)
        assert len(groups) == 0

    def test_files_sorted_by_filename(self, detector):
        """Files within a burst group are sorted by filename."""
        ts = datetime(2024, 3, 15, 14, 30, 0)
        records = [
            _rec("/photos/IMG_001_BURST003.jpg", dt=ts),
            _rec("/photos/IMG_001_BURST001.jpg", dt=ts),
            _rec("/photos/IMG_001_BURST002.jpg", dt=ts),
        ]
        groups = detector.detect_bursts(records)
        assert len(groups) == 1
        names = [os.path.basename(f) for f in groups[0].files]
        assert names == sorted(names, key=str.lower)


# ===========================================================================
# 4.  Timestamp grouping
# ===========================================================================

class TestTimestampGrouping:

    def test_same_second_same_camera_3_files(self, detector):
        """≥3 files at the same second from the same camera form a group."""
        ts = datetime(2024, 3, 15, 14, 30, 0)
        records = [
            _rec("/photos/IMG_001.jpg", "Canon", "EOS R5", ts),
            _rec("/photos/IMG_002.jpg", "Canon", "EOS R5", ts),
            _rec("/photos/IMG_003.jpg", "Canon", "EOS R5", ts),
        ]
        groups = detector.detect_bursts(records)
        assert len(groups) == 1
        assert len(groups[0].files) == 3
        assert groups[0].camera == "Canon - EOS R5"
        assert groups[0].date == ts

    def test_two_files_same_second_not_grouped(self, detector):
        """Only 2 files at the same second is NOT enough for timestamp layer."""
        ts = datetime(2024, 3, 15, 14, 30, 0)
        records = [
            _rec("/photos/IMG_001.jpg", "Canon", "EOS R5", ts),
            _rec("/photos/IMG_002.jpg", "Canon", "EOS R5", ts),
        ]
        groups = detector.detect_bursts(records)
        assert len(groups) == 0

    def test_different_seconds_not_grouped(self, detector):
        """Files at different seconds are separate — not grouped."""
        records = [
            _rec("/photos/IMG_001.jpg", "Canon", "EOS R5", datetime(2024, 3, 15, 14, 30, 0)),
            _rec("/photos/IMG_002.jpg", "Canon", "EOS R5", datetime(2024, 3, 15, 14, 30, 1)),
            _rec("/photos/IMG_003.jpg", "Canon", "EOS R5", datetime(2024, 3, 15, 14, 30, 2)),
        ]
        groups = detector.detect_bursts(records)
        assert len(groups) == 0

    def test_mixed_cameras_not_grouped(self, detector):
        """Files from different cameras at the same timestamp are separate."""
        ts = datetime(2024, 3, 15, 14, 30, 0)
        records = [
            _rec("/photos/IMG_001.jpg", "Canon", "EOS R5", ts),
            _rec("/photos/IMG_002.jpg", "Canon", "EOS R5", ts),
            _rec("/photos/IMG_003.jpg", "Nikon", "Z8", ts),
            _rec("/photos/IMG_004.jpg", "Nikon", "Z8", ts),
            _rec("/photos/IMG_005.jpg", "Nikon", "Z8", ts),
        ]
        groups = detector.detect_bursts(records)
        # Canon only has 2 → not enough.  Nikon has 3 → one group.
        assert len(groups) == 1
        assert groups[0].camera == "Nikon - Z8"
        assert len(groups[0].files) == 3

    def test_no_camera_files_grouped_together(self, detector):
        """Files with both camera=None are grouped together."""
        ts = datetime(2024, 6, 1, 9, 0, 0)
        records = [
            _rec("/a.jpg", dt=ts),
            _rec("/b.jpg", dt=ts),
            _rec("/c.jpg", dt=ts),
        ]
        groups = detector.detect_bursts(records)
        assert len(groups) == 1
        assert groups[0].camera is None

    def test_no_date_not_grouped(self, detector):
        """Files without a date are excluded from timestamp grouping."""
        records = [
            _rec("/a.jpg", "Canon", "EOS R5"),
            _rec("/b.jpg", "Canon", "EOS R5"),
            _rec("/c.jpg", "Canon", "EOS R5"),
        ]
        groups = detector.detect_bursts(records)
        assert len(groups) == 0

    def test_microseconds_ignored(self, detector):
        """Timestamps differing only in microseconds are grouped together."""
        records = [
            _rec("/a.jpg", "Sony", "A7R IV", datetime(2024, 1, 1, 12, 0, 0, 0)),
            _rec("/b.jpg", "Sony", "A7R IV", datetime(2024, 1, 1, 12, 0, 0, 100000)),
            _rec("/c.jpg", "Sony", "A7R IV", datetime(2024, 1, 1, 12, 0, 0, 500000)),
        ]
        groups = detector.detect_bursts(records)
        assert len(groups) == 1

    def test_four_files_from_same_camera(self, detector):
        ts = datetime(2024, 3, 15, 14, 30, 0)
        records = [
            _rec("/IMG_001.jpg", "Samsung", "SM-G998B", ts),
            _rec("/IMG_002.jpg", "Samsung", "SM-G998B", ts),
            _rec("/IMG_003.jpg", "Samsung", "SM-G998B", ts),
            _rec("/IMG_004.jpg", "Samsung", "SM-G998B", ts),
        ]
        groups = detector.detect_bursts(records)
        assert len(groups) == 1
        assert len(groups[0].files) == 4


# ===========================================================================
# 5.  Single files are not grouped
# ===========================================================================

class TestSingleFiles:

    def test_single_file_no_burst(self, detector):
        records = [
            _rec("/photos/IMG_001.jpg", "Canon", "EOS R5", datetime(2024, 1, 1, 12, 0, 0)),
        ]
        groups = detector.detect_bursts(records)
        assert len(groups) == 0

    def test_empty_input(self, detector):
        groups = detector.detect_bursts([])
        assert groups == []

    def test_all_unique_timestamps(self, detector):
        """Every file has a unique timestamp — no groups possible."""
        records = [
            _rec("/a.jpg", "Canon", "EOS R5", datetime(2024, 1, 1, 12, 0, 0)),
            _rec("/b.jpg", "Canon", "EOS R5", datetime(2024, 1, 1, 12, 0, 1)),
            _rec("/c.jpg", "Canon", "EOS R5", datetime(2024, 1, 1, 12, 0, 2)),
        ]
        groups = detector.detect_bursts(records)
        assert len(groups) == 0


# ===========================================================================
# 6.  Layer priority / overlap
# ===========================================================================

class TestLayerPriority:

    def test_file_belongs_to_one_group_only(self, detector):
        """A file claimed by EXIF layer is not re-grouped by later layers."""
        ts = datetime(2024, 3, 15, 14, 30, 0)
        # These files have both EXIF burst tags AND a burst filename pattern
        # AND the same timestamp.  They should appear in exactly one group.
        records = [
            _rec("/IMG_001_BURST001.jpg", "Canon", "EOS R5", ts, {"BurstMode": 1}),
            _rec("/IMG_001_BURST002.jpg", "Canon", "EOS R5", ts, {"BurstMode": 1}),
            _rec("/IMG_001_BURST003.jpg", "Canon", "EOS R5", ts, {"BurstMode": 1}),
        ]
        groups = detector.detect_bursts(records)
        # Should only produce ONE group (from EXIF layer), not duplicates.
        all_files = [f for g in groups for f in g.files]
        assert len(all_files) == len(set(all_files))

    def test_exif_burst_takes_priority(self, detector):
        """EXIF burst detection runs first; filename/timestamp layers get leftovers."""
        ts = datetime(2024, 3, 15, 14, 30, 0)
        records = [
            # These two have EXIF burst tags → claimed by layer 1
            _rec("/IMG_001.jpg", "Canon", "EOS R5", ts, {"BurstMode": 1}),
            _rec("/IMG_002.jpg", "Canon", "EOS R5", ts, {"BurstMode": 1}),
            # These three share the timestamp but NOT burst tags
            _rec("/IMG_003.jpg", "Canon", "EOS R5", ts),
            _rec("/IMG_004.jpg", "Canon", "EOS R5", ts),
            _rec("/IMG_005.jpg", "Canon", "EOS R5", ts),
        ]
        groups = detector.detect_bursts(records)

        # Layer 1: EXIF group of 2
        # Layer 3: timestamp group of 3 (IMG_003–005)
        exif_group = next(g for g in groups if len(g.files) == 2)
        ts_group = next(g for g in groups if len(g.files) == 3)
        assert exif_group is not None
        assert ts_group is not None

    def test_filename_takes_priority_over_timestamp(self, detector):
        """Filename-pattern matches are consumed before timestamp grouping."""
        ts = datetime(2024, 3, 15, 14, 30, 0)
        records = [
            _rec("/photos/IMG_001_BURST001.jpg", "Canon", "EOS R5", ts),
            _rec("/photos/IMG_001_BURST002.jpg", "Canon", "EOS R5", ts),
            # Additional files at same timestamp without burst keyword
            _rec("/photos/IMG_002.jpg", "Canon", "EOS R5", ts),
            _rec("/photos/IMG_003.jpg", "Canon", "EOS R5", ts),
            _rec("/photos/IMG_004.jpg", "Canon", "EOS R5", ts),
        ]
        groups = detector.detect_bursts(records)
        # Pattern group: 2 BURST files.
        # Timestamp group: 3 remaining files (IMG_002, 003, 004).
        assert len(groups) == 2
        all_files = [f for g in groups for f in g.files]
        assert len(all_files) == 5
        assert len(set(all_files)) == 5


# ===========================================================================
# 7.  BurstGroup dataclass
# ===========================================================================

class TestBurstGroup:

    def test_fields(self):
        g = BurstGroup(
            files=["/a.jpg", "/b.jpg"],
            date=datetime(2024, 1, 1, 12, 0, 0),
            camera="Canon - EOS R5",
            sequence_start=0,
        )
        assert g.files == ["/a.jpg", "/b.jpg"]
        assert g.camera == "Canon - EOS R5"
        assert g.sequence_start == 0

    def test_no_camera(self):
        g = BurstGroup(
            files=["/a.jpg"],
            date=datetime.min,
            camera=None,
            sequence_start=5,
        )
        assert g.camera is None
        assert g.sequence_start == 5
