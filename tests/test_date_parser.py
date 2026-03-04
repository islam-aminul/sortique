"""Tests for sortique.engine.metadata.date_parser."""

from __future__ import annotations

from datetime import datetime

import pytest

from sortique.constants import DateSource
from sortique.data.config_manager import ConfigManager
from sortique.engine.metadata.date_parser import DEFAULT_PATTERNS, DateParser, DateResult
from sortique.engine.metadata.exif_extractor import ExifResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def config(tmp_path):
    """ConfigManager backed by a throw-away directory."""
    return ConfigManager(config_dir=str(tmp_path / "cfg"))


@pytest.fixture()
def parser(config):
    return DateParser(config)


# ===========================================================================
# 1. EXIF date priority
# ===========================================================================

class TestExifPriority:
    """_from_exif must honour DateTimeOriginal > Digitized > Modified."""

    def test_prefers_date_original(self, parser, tmp_path):
        f = str(tmp_path / "photo.jpg")
        exif = ExifResult(
            date_original=datetime(2024, 3, 15, 14, 30, 0),
            date_digitized=datetime(2024, 3, 16, 10, 0, 0),
            date_modified=datetime(2024, 3, 17, 8, 0, 0),
        )
        result = parser.extract_date(f, exif_result=exif)
        assert result.date == datetime(2024, 3, 15, 14, 30, 0)
        assert result.source == DateSource.METADATA
        assert result.confidence == 1.0

    def test_falls_back_to_digitized(self, parser, tmp_path):
        f = str(tmp_path / "photo.jpg")
        exif = ExifResult(
            date_original=None,
            date_digitized=datetime(2024, 3, 16, 10, 0, 0),
            date_modified=datetime(2024, 3, 17, 8, 0, 0),
        )
        result = parser.extract_date(f, exif_result=exif)
        assert result.date == datetime(2024, 3, 16, 10, 0, 0)
        assert result.source == DateSource.METADATA

    def test_falls_back_to_modified(self, parser, tmp_path):
        f = str(tmp_path / "photo.jpg")
        exif = ExifResult(
            date_original=None,
            date_digitized=None,
            date_modified=datetime(2024, 3, 17, 8, 0, 0),
        )
        result = parser.extract_date(f, exif_result=exif)
        assert result.date == datetime(2024, 3, 17, 8, 0, 0)

    def test_no_exif_dates_returns_empty(self, parser):
        exif = ExifResult()
        result = parser._from_exif(exif)
        assert result.date is None
        assert result.source == DateSource.NONE

    def test_timezone_offset_preserved(self, parser, tmp_path):
        f = str(tmp_path / "photo.jpg")
        exif = ExifResult(
            date_original=datetime(2024, 3, 15, 14, 30, 0),
            timezone_offset="+05:30",
        )
        result = parser.extract_date(f, exif_result=exif)
        assert result.timezone_offset == "+05:30"

    def test_exif_with_only_modified_still_works(self, parser, tmp_path):
        f = str(tmp_path / "photo.jpg")
        exif = ExifResult(date_modified=datetime(2023, 12, 25, 9, 0, 0))
        result = parser.extract_date(f, exif_result=exif)
        assert result.date == datetime(2023, 12, 25, 9, 0, 0)
        assert result.confidence == 1.0


# ===========================================================================
# 2. Filename patterns
# ===========================================================================

class TestFilenamePatterns:
    """Regex extraction from the filename stem."""

    def test_dash_separated_datetime(self, parser, tmp_path):
        """Pattern 1: '2024-03-15 14-30-00 photo.jpg'"""
        f = str(tmp_path / "2024-03-15 14-30-00 photo.jpg")
        result = parser.extract_date(f)
        assert result.date == datetime(2024, 3, 15, 14, 30, 0)
        assert result.source == DateSource.PARSED
        assert result.confidence == 0.8

    def test_compact_datetime(self, parser, tmp_path):
        """Pattern 2: '20240315_143000.jpg'"""
        f = str(tmp_path / "20240315_143000.jpg")
        result = parser.extract_date(f)
        assert result.date == datetime(2024, 3, 15, 14, 30, 0)
        assert result.source == DateSource.PARSED
        assert result.confidence == 0.8

    def test_date_only(self, parser, tmp_path):
        """Pattern 3: '2024-03-15 photo.jpg'"""
        f = str(tmp_path / "2024-03-15 photo.jpg")
        result = parser.extract_date(f)
        assert result.date == datetime(2024, 3, 15, 0, 0, 0)
        assert result.source == DateSource.PARSED
        assert result.confidence == 0.8

    def test_european_day_first(self, parser, tmp_path):
        """Pattern 4: '15-03-2024 photo.jpg' — unambiguous (day > 12)."""
        f = str(tmp_path / "15-03-2024 photo.jpg")
        result = parser.extract_date(f)
        assert result.date == datetime(2024, 3, 15, 0, 0, 0)
        assert result.confidence == 0.8  # day > 12 → not ambiguous

    def test_img_prefix_compact(self, parser, tmp_path):
        """Common camera naming: 'IMG_20240315_143000.jpg'"""
        f = str(tmp_path / "IMG_20240315_143000.jpg")
        result = parser.extract_date(f)
        assert result.date == datetime(2024, 3, 15, 14, 30, 0)

    def test_underscore_separated_datetime(self, parser, tmp_path):
        """Pattern 1 with underscores: '2024_03_15_14_30_00.jpg'"""
        f = str(tmp_path / "2024_03_15_14_30_00.jpg")
        result = parser.extract_date(f)
        assert result.date == datetime(2024, 3, 15, 14, 30, 0)

    def test_dot_separated_date(self, parser, tmp_path):
        """Pattern 3 with dots: '2024.03.15 photo.jpg'"""
        f = str(tmp_path / "2024.03.15 photo.jpg")
        result = parser.extract_date(f)
        assert result.date == datetime(2024, 3, 15, 0, 0, 0)

    def test_t_separator_between_date_and_time(self, parser, tmp_path):
        """ISO-style T separator: '2024-03-15T14-30-00.jpg'"""
        f = str(tmp_path / "2024-03-15T14-30-00.jpg")
        result = parser.extract_date(f)
        assert result.date == datetime(2024, 3, 15, 14, 30, 0)

    def test_no_date_in_filename(self, parser, tmp_path):
        f = str(tmp_path / "vacation_photo.jpg")
        result = parser._from_filename(f)
        assert result.date is None
        assert result.source == DateSource.NONE


# ===========================================================================
# 3. Folder name parsing
# ===========================================================================

class TestFolderName:
    """Regex extraction from the parent directory name."""

    def test_date_in_folder_name(self, parser, tmp_path):
        folder = tmp_path / "2024-03-15 Trip"
        folder.mkdir()
        f = str(folder / "photo.jpg")
        result = parser._from_folder_name(f)
        assert result.date == datetime(2024, 3, 15)
        assert result.source == DateSource.PARSED
        assert result.confidence == 0.6

    def test_no_date_in_folder(self, parser, tmp_path):
        folder = tmp_path / "vacation"
        folder.mkdir()
        f = str(folder / "photo.jpg")
        result = parser._from_folder_name(f)
        assert result.date is None

    def test_folder_with_full_datetime(self, parser, tmp_path):
        folder = tmp_path / "2024-03-15 14-30-00 Birthday"
        folder.mkdir()
        f = str(folder / "photo.jpg")
        result = parser._from_folder_name(f)
        assert result.date == datetime(2024, 3, 15, 14, 30, 0)
        assert result.confidence == 0.6

    def test_folder_compact_date(self, parser, tmp_path):
        folder = tmp_path / "20240315_trip"
        folder.mkdir()
        f = str(folder / "photo.jpg")
        # "20240315_trip" — pattern 2 needs 14 digits total, pattern 3
        # needs separators. Won't match any pattern for date-only.
        result = parser._from_folder_name(f)
        # Compact date-only (no time component) is NOT a default pattern,
        # so no match expected.
        assert result.date is None


# ===========================================================================
# 4. Nearby file inference
# ===========================================================================

class TestNearbyFiles:
    """Sibling-based date inference with >50 % agreement threshold."""

    def test_majority_agreement(self, parser):
        siblings = [
            datetime(2024, 3, 15, 10, 0, 0),
            datetime(2024, 3, 15, 11, 0, 0),
            datetime(2024, 3, 15, 12, 0, 0),
            datetime(2024, 3, 16, 10, 0, 0),
        ]
        result = parser._from_nearby_files(siblings)
        assert result.date is not None
        # Returned date is the calendar day (time zeroed).
        assert result.date == datetime(2024, 3, 15, 0, 0, 0)
        assert result.source == DateSource.INFERRED
        assert result.confidence == 0.3

    def test_no_majority(self, parser):
        siblings = [
            datetime(2024, 3, 15, 10, 0, 0),
            datetime(2024, 3, 16, 10, 0, 0),
            datetime(2024, 3, 17, 10, 0, 0),
            datetime(2024, 3, 18, 10, 0, 0),
        ]
        result = parser._from_nearby_files(siblings)
        assert result.date is None

    def test_exactly_50_percent_not_enough(self, parser):
        """50 % is NOT > 50 %."""
        siblings = [
            datetime(2024, 3, 15, 10, 0, 0),
            datetime(2024, 3, 16, 10, 0, 0),
        ]
        result = parser._from_nearby_files(siblings)
        assert result.date is None

    def test_empty_siblings(self, parser):
        result = parser._from_nearby_files([])
        assert result.date is None

    def test_single_sibling_counts_as_majority(self, parser):
        """1 out of 1 = 100 % > 50 %."""
        siblings = [datetime(2024, 7, 4, 15, 0, 0)]
        result = parser._from_nearby_files(siblings)
        assert result.date == datetime(2024, 7, 4, 0, 0, 0)
        assert result.source == DateSource.INFERRED

    def test_different_times_same_day(self, parser):
        """All siblings on the same day but different times → 100 % agreement."""
        siblings = [
            datetime(2024, 1, 1, 8, 0, 0),
            datetime(2024, 1, 1, 12, 0, 0),
            datetime(2024, 1, 1, 18, 30, 0),
        ]
        result = parser._from_nearby_files(siblings)
        assert result.date == datetime(2024, 1, 1, 0, 0, 0)


# ===========================================================================
# 5. Invalid date rejection
# ===========================================================================

class TestInvalidDates:
    """_validate_date and full pipeline must reject impossible dates."""

    def test_month_13_rejected(self, parser, tmp_path):
        f = str(tmp_path / "2024-13-15 photo.jpg")
        result = parser.extract_date(f)
        assert result.date is None

    def test_day_32_rejected(self, parser, tmp_path):
        f = str(tmp_path / "2024-03-32 photo.jpg")
        result = parser.extract_date(f)
        assert result.date is None

    def test_year_too_old(self, parser, tmp_path):
        f = str(tmp_path / "1899-01-01 photo.jpg")
        result = parser.extract_date(f)
        assert result.date is None

    def test_year_too_future(self, parser, tmp_path):
        f = str(tmp_path / "2101-01-01 photo.jpg")
        result = parser.extract_date(f)
        assert result.date is None

    def test_hour_24_rejected(self):
        assert not DateParser._validate_date(2024, 3, 15, 24, 0, 0)

    def test_minute_60_rejected(self):
        assert not DateParser._validate_date(2024, 3, 15, 12, 60, 0)

    def test_second_60_rejected(self):
        assert not DateParser._validate_date(2024, 3, 15, 12, 30, 60)

    def test_month_0_rejected(self):
        assert not DateParser._validate_date(2024, 0, 15)

    def test_day_0_rejected(self):
        assert not DateParser._validate_date(2024, 3, 0)

    def test_feb_30_rejected_by_datetime(self, parser, tmp_path):
        """Feb 30 passes _validate_date (day ≤ 31) but fails datetime()."""
        f = str(tmp_path / "2024-02-30 photo.jpg")
        result = parser.extract_date(f)
        assert result.date is None

    def test_valid_boundary_low(self):
        assert DateParser._validate_date(1900, 1, 1, 0, 0, 0)

    def test_valid_boundary_high(self):
        assert DateParser._validate_date(2100, 12, 31, 23, 59, 59)


# ===========================================================================
# 6. Ambiguous dates
# ===========================================================================

class TestAmbiguousDates:
    """DD-MM vs MM-DD disambiguation."""

    def test_unambiguous_day_over_12(self, parser, tmp_path):
        """15-03-2024 — day is 15, cannot be month → full confidence."""
        f = str(tmp_path / "15-03-2024 photo.jpg")
        result = parser.extract_date(f)
        assert result.date == datetime(2024, 3, 15)
        assert result.confidence == 0.8

    def test_ambiguous_both_under_12(self, parser, tmp_path):
        """01-02-2024 — day=01, month=02 per pattern but could be swapped."""
        f = str(tmp_path / "01-02-2024 photo.jpg")
        result = parser.extract_date(f)
        assert result.date is not None
        # Parsed as Feb 1 (DD-MM-YYYY pattern), but confidence halved.
        assert result.date == datetime(2024, 2, 1)
        assert result.confidence == pytest.approx(0.4)

    def test_iso_format_not_ambiguous(self, parser, tmp_path):
        """2024-01-02 — ISO (YYYY-MM-DD) is never ambiguous."""
        f = str(tmp_path / "2024-01-02 photo.jpg")
        result = parser.extract_date(f)
        assert result.date == datetime(2024, 1, 2)
        assert result.confidence == 0.8

    def test_ambiguous_symmetric_values(self, parser, tmp_path):
        """05-05-2024 — day=05, month=05; same value so no practical issue."""
        f = str(tmp_path / "05-05-2024 photo.jpg")
        result = parser.extract_date(f)
        assert result.date == datetime(2024, 5, 5)
        # Still flagged as ambiguous (both ≤ 12, DD-MM pattern).
        assert result.confidence == pytest.approx(0.4)


# ===========================================================================
# 7. Full fallback chain
# ===========================================================================

class TestFallbackChain:
    """Integration: the four tiers cascade correctly."""

    def test_exif_takes_priority_over_filename(self, parser, tmp_path):
        f = str(tmp_path / "2020-01-01 photo.jpg")
        exif = ExifResult(date_original=datetime(2024, 6, 15, 10, 0, 0))
        result = parser.extract_date(f, exif_result=exif)
        assert result.date == datetime(2024, 6, 15, 10, 0, 0)
        assert result.source == DateSource.METADATA

    def test_filename_when_no_exif(self, parser, tmp_path):
        f = str(tmp_path / "2024-03-15 photo.jpg")
        result = parser.extract_date(f)
        assert result.date == datetime(2024, 3, 15)
        assert result.source == DateSource.PARSED
        assert result.confidence == 0.8

    def test_folder_when_no_filename_date(self, parser, tmp_path):
        folder = tmp_path / "2024-03-15 Trip"
        folder.mkdir()
        f = str(folder / "photo.jpg")
        result = parser.extract_date(f)
        assert result.date == datetime(2024, 3, 15)
        assert result.source == DateSource.PARSED
        assert result.confidence == 0.6

    def test_nearby_when_nothing_else(self, parser, tmp_path):
        folder = tmp_path / "nondated"
        folder.mkdir()
        f = str(folder / "photo.jpg")
        siblings = [
            datetime(2024, 3, 15, 10, 0, 0),
            datetime(2024, 3, 15, 11, 0, 0),
            datetime(2024, 3, 15, 12, 0, 0),
        ]
        result = parser.extract_date(f, sibling_files=siblings)
        assert result.date is not None
        assert result.source == DateSource.INFERRED
        assert result.confidence == 0.3

    def test_no_date_found_anywhere(self, parser, tmp_path):
        folder = tmp_path / "misc"
        folder.mkdir()
        f = str(folder / "mystery_file.jpg")
        result = parser.extract_date(f)
        assert result.date is None
        assert result.source == DateSource.NONE
        assert result.confidence == 0.0

    def test_exif_all_none_falls_through_to_filename(self, parser, tmp_path):
        """ExifResult present but all dates None → filename takes over."""
        f = str(tmp_path / "2024-07-04 fireworks.jpg")
        exif = ExifResult()  # all dates None
        result = parser.extract_date(f, exif_result=exif)
        assert result.date == datetime(2024, 7, 4)
        assert result.source == DateSource.PARSED

    def test_filename_beats_folder(self, parser, tmp_path):
        """When filename has a date, folder is never consulted."""
        folder = tmp_path / "1999-01-01 OldFolder"
        folder.mkdir()
        f = str(folder / "2024-06-15 photo.jpg")
        result = parser.extract_date(f)
        assert result.date == datetime(2024, 6, 15)
        assert result.confidence == 0.8


# ===========================================================================
# 8. DateResult dataclass
# ===========================================================================

class TestDateResult:
    """Verify default construction and field semantics."""

    def test_defaults(self):
        r = DateResult()
        assert r.date is None
        assert r.source == DateSource.NONE
        assert r.timezone_offset is None
        assert r.confidence == 0.0

    def test_full_construction(self):
        r = DateResult(
            date=datetime(2024, 1, 1),
            source=DateSource.METADATA,
            timezone_offset="-05:00",
            confidence=1.0,
        )
        assert r.date == datetime(2024, 1, 1)
        assert r.source == DateSource.METADATA
        assert r.timezone_offset == "-05:00"
        assert r.confidence == 1.0


# ===========================================================================
# 9. Custom config patterns
# ===========================================================================

class TestCustomConfig:
    """Verify that user-supplied config patterns are respected."""

    def test_custom_patterns_used(self, tmp_path):
        config = ConfigManager(config_dir=str(tmp_path / "cfg"))
        # Add a non-standard pattern: "photo_YYYY_MM_DD"
        config.set("date_regex_patterns", [
            r"photo_(?P<Y>\d{4})_(?P<m>\d{2})_(?P<d>\d{2})",
        ])
        parser = DateParser(config)
        f = str(tmp_path / "photo_2024_08_20.jpg")
        result = parser.extract_date(f)
        assert result.date == datetime(2024, 8, 20)

    def test_empty_patterns_falls_back_to_defaults(self, tmp_path):
        config = ConfigManager(config_dir=str(tmp_path / "cfg"))
        config.set("date_regex_patterns", [])
        parser = DateParser(config)
        # Should still match default patterns.
        f = str(tmp_path / "2024-03-15 photo.jpg")
        result = parser.extract_date(f)
        assert result.date == datetime(2024, 3, 15)

    def test_default_patterns_constant_matches_defaults_json(self, config):
        """DEFAULT_PATTERNS list should compile to the same regexes as defaults.json."""
        from_config = config.date_regex_patterns
        from_constant = [__import__("re").compile(p) for p in DEFAULT_PATTERNS]
        assert len(from_config) == len(from_constant)
        for cfg, const in zip(from_config, from_constant):
            assert cfg.pattern == const.pattern
