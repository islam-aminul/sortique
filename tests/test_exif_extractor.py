"""Tests for sortique.engine.metadata.exif_extractor."""

from __future__ import annotations

import io
import os
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import piexif
import pytest
from PIL import Image

from sortique.constants import ExifStatus
from sortique.engine.metadata.exif_extractor import (
    ExifExtractor,
    ExifResult,
    _decode_bytes,
    _rational_to_float,
)


# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_EXIF_SAMPLE = _FIXTURES / "exif_sample.jpg"


# ---------------------------------------------------------------------------
# Programmatic fixture builders (deterministic, self-contained)
# ---------------------------------------------------------------------------


def _build_jpeg_with_exif(
    tmp_path: Path,
    *,
    make: bytes = b"Canon",
    model: bytes = b"Canon EOS R5",
    software: bytes = b"Adobe Lightroom 6.0",
    date_original: bytes = b"2024:06:15 14:30:00",
    date_digitized: bytes = b"2024:06:15 14:30:00",
    date_modified: bytes = b"2024:06:15 14:30:00",
    offset_time: bytes = b"+05:30",
    gps_lat: tuple = ((40, 1), (26, 1), (46, 1)),
    gps_lat_ref: bytes = b"N",
    gps_lon: tuple = ((79, 1), (58, 1), (36, 1)),
    gps_lon_ref: bytes = b"W",
    orientation: int = 1,
    include_thumbnail: bool = True,
    width: int = 100,
    height: int = 75,
) -> str:
    """Create a JPEG with specific EXIF metadata and return its path."""
    img = Image.new("RGB", (width, height), color=(255, 128, 0))

    exif_dict: dict = {
        "0th": {
            piexif.ImageIFD.Make: make,
            piexif.ImageIFD.Model: model,
            piexif.ImageIFD.Software: software,
            piexif.ImageIFD.Orientation: orientation,
            piexif.ImageIFD.DateTime: date_modified,
        },
        "Exif": {
            piexif.ExifIFD.DateTimeOriginal: date_original,
            piexif.ExifIFD.DateTimeDigitized: date_digitized,
            piexif.ExifIFD.OffsetTimeOriginal: offset_time,
            piexif.ExifIFD.PixelXDimension: width,
            piexif.ExifIFD.PixelYDimension: height,
        },
        "GPS": {
            piexif.GPSIFD.GPSLatitudeRef: gps_lat_ref,
            piexif.GPSIFD.GPSLatitude: gps_lat,
            piexif.GPSIFD.GPSLongitudeRef: gps_lon_ref,
            piexif.GPSIFD.GPSLongitude: gps_lon,
        },
        "1st": {},
        "thumbnail": None,
    }

    if include_thumbnail:
        thumb = Image.new("RGB", (16, 12), color=(128, 128, 128))
        buf = io.BytesIO()
        thumb.save(buf, "JPEG")
        exif_dict["1st"] = {piexif.ImageIFD.Compression: 6}
        exif_dict["thumbnail"] = buf.getvalue()

    exif_bytes = piexif.dump(exif_dict)
    out = str(tmp_path / "test_exif.jpg")
    img.save(out, "JPEG", exif=exif_bytes)
    return out


def _build_jpeg_no_exif(tmp_path: Path) -> str:
    """Create a plain JPEG with no EXIF data at all."""
    img = Image.new("RGB", (50, 50), color=(0, 0, 0))
    out = str(tmp_path / "no_exif.jpg")
    img.save(out, "JPEG")
    # Strip any default EXIF Pillow might add.
    piexif.remove(out)
    return out


def _build_corrupt_exif_jpeg(tmp_path: Path) -> str:
    """Create a JPEG that Pillow can open but whose EXIF block is garbage."""
    # Start with a valid JPEG.
    img = Image.new("RGB", (20, 20), color=(0, 255, 0))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    raw = buf.getvalue()

    # Inject a corrupt APP1 segment right after the SOI marker.
    corrupt = (
        raw[:2]                 # FF D8 (SOI)
        + b"\xff\xe1"           # APP1 marker
        + b"\x00\x10"           # length = 16
        + b"Exif\x00\x00"       # EXIF header (6 bytes)
        + b"\xff" * 8            # garbage TIFF header
        + raw[2:]               # rest of JPEG
    )

    out = str(tmp_path / "corrupt_exif.jpg")
    with open(out, "wb") as f:
        f.write(corrupt)
    return out


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def extractor():
    return ExifExtractor()


@pytest.fixture()
def full_exif_jpg(tmp_path):
    """JPEG with complete EXIF data (make, model, dates, GPS, thumbnail)."""
    return _build_jpeg_with_exif(tmp_path)


@pytest.fixture()
def no_exif_jpg(tmp_path):
    """JPEG with all EXIF data stripped."""
    return _build_jpeg_no_exif(tmp_path)


@pytest.fixture()
def corrupt_exif_jpg(tmp_path):
    """JPEG with a corrupt EXIF APP1 block."""
    return _build_corrupt_exif_jpeg(tmp_path)


# ===================================================================
# 1. Full EXIF extraction (real JPEG with all fields)
# ===================================================================


class TestFullExifExtraction:
    """Test with a JPEG that has all relevant EXIF tags populated."""

    def test_status_ok(self, extractor, full_exif_jpg):
        result = extractor.extract(full_exif_jpg)
        assert result.status == ExifStatus.OK

    def test_camera_make(self, extractor, full_exif_jpg):
        result = extractor.extract(full_exif_jpg)
        assert result.make == "Canon"

    def test_camera_model(self, extractor, full_exif_jpg):
        result = extractor.extract(full_exif_jpg)
        assert result.model == "Canon EOS R5"

    def test_software(self, extractor, full_exif_jpg):
        result = extractor.extract(full_exif_jpg)
        assert result.software == "Adobe Lightroom 6.0"

    def test_date_original(self, extractor, full_exif_jpg):
        result = extractor.extract(full_exif_jpg)
        assert result.date_original == datetime(2024, 6, 15, 14, 30, 0)

    def test_date_digitized(self, extractor, full_exif_jpg):
        result = extractor.extract(full_exif_jpg)
        assert result.date_digitized == datetime(2024, 6, 15, 14, 30, 0)

    def test_date_modified(self, extractor, full_exif_jpg):
        result = extractor.extract(full_exif_jpg)
        assert result.date_modified == datetime(2024, 6, 15, 14, 30, 0)

    def test_gps_latitude(self, extractor, full_exif_jpg):
        result = extractor.extract(full_exif_jpg)
        # 40 + 26/60 + 46/3600 = 40.44611...
        assert result.gps_lat is not None
        assert abs(result.gps_lat - 40.446111) < 0.001

    def test_gps_longitude(self, extractor, full_exif_jpg):
        result = extractor.extract(full_exif_jpg)
        # -(79 + 58/60 + 36/3600) = -79.97667
        assert result.gps_lon is not None
        assert abs(result.gps_lon - (-79.976667)) < 0.001

    def test_orientation(self, extractor, full_exif_jpg):
        result = extractor.extract(full_exif_jpg)
        assert result.orientation == 1

    def test_dimensions(self, extractor, full_exif_jpg):
        result = extractor.extract(full_exif_jpg)
        assert result.width == 100
        assert result.height == 75

    def test_timezone_offset(self, extractor, full_exif_jpg):
        result = extractor.extract(full_exif_jpg)
        assert result.timezone_offset == "+05:30"

    def test_has_thumbnail(self, extractor, full_exif_jpg):
        result = extractor.extract(full_exif_jpg)
        assert result.has_thumbnail is True

    def test_no_error(self, extractor, full_exif_jpg):
        result = extractor.extract(full_exif_jpg)
        assert result.error_message is None


class TestPhysicalFixture:
    """Verify extraction works on the pre-built fixture file."""

    @pytest.mark.skipif(
        not _EXIF_SAMPLE.exists(),
        reason="exif_sample.jpg fixture not present",
    )
    def test_fixture_file_extraction(self, extractor):
        result = extractor.extract(str(_EXIF_SAMPLE))
        assert result.status == ExifStatus.OK
        assert result.make == "Canon"
        assert result.model == "Canon EOS R5"
        assert result.date_original == datetime(2024, 6, 15, 14, 30, 0)
        assert result.has_thumbnail is True


# ===================================================================
# 2. No EXIF data
# ===================================================================


class TestNoExifData:
    """A JPEG with all EXIF stripped should return status=NONE."""

    def test_status_none(self, extractor, no_exif_jpg):
        result = extractor.extract(no_exif_jpg)
        assert result.status == ExifStatus.NONE

    def test_make_is_none(self, extractor, no_exif_jpg):
        result = extractor.extract(no_exif_jpg)
        assert result.make is None

    def test_dates_are_none(self, extractor, no_exif_jpg):
        result = extractor.extract(no_exif_jpg)
        assert result.date_original is None
        assert result.date_digitized is None
        assert result.date_modified is None

    def test_gps_is_none(self, extractor, no_exif_jpg):
        result = extractor.extract(no_exif_jpg)
        assert result.gps_lat is None
        assert result.gps_lon is None

    def test_dimensions_still_present(self, extractor, no_exif_jpg):
        """Pillow can always read pixel dimensions even without EXIF."""
        result = extractor.extract(no_exif_jpg)
        assert result.width == 50
        assert result.height == 50

    def test_no_thumbnail(self, extractor, no_exif_jpg):
        result = extractor.extract(no_exif_jpg)
        assert result.has_thumbnail is False


# ===================================================================
# 3. GPS DMS-to-decimal conversion
# ===================================================================


class TestGpsConversion:
    """Unit tests for the DMS → decimal-degrees converter."""

    def test_north_positive(self, extractor):
        dms = ((40, 1), (26, 1), (46, 1))
        result = extractor._gps_to_decimal(dms, "N")
        assert abs(result - 40.446111) < 0.0001

    def test_south_negative(self, extractor):
        dms = ((33, 1), (51, 1), (54, 1))
        result = extractor._gps_to_decimal(dms, "S")
        # -(33 + 51/60 + 54/3600) = -33.865
        assert result < 0
        assert abs(result - (-33.865)) < 0.001

    def test_east_positive(self, extractor):
        dms = ((151, 1), (12, 1), (36, 1))
        result = extractor._gps_to_decimal(dms, "E")
        # 151 + 12/60 + 36/3600 = 151.21
        assert result > 0
        assert abs(result - 151.21) < 0.001

    def test_west_negative(self, extractor):
        dms = ((79, 1), (58, 1), (36, 1))
        result = extractor._gps_to_decimal(dms, "W")
        assert result < 0
        assert abs(result - (-79.976667)) < 0.001

    def test_fractional_seconds(self, extractor):
        """Seconds with a non-1 denominator (e.g. 4680/100 = 46.80'')."""
        dms = ((40, 1), (26, 1), (4680, 100))
        result = extractor._gps_to_decimal(dms, "N")
        # 40 + 26/60 + 46.8/3600 = 40.44633...
        assert abs(result - 40.44633) < 0.0001

    def test_zero_coordinates(self, extractor):
        dms = ((0, 1), (0, 1), (0, 1))
        result = extractor._gps_to_decimal(dms, "N")
        assert result == 0.0

    def test_high_precision_rational(self, extractor):
        """Rational components with large denominators for sub-second precision."""
        # 48 deg 51' 29.592"  =>  48 + 51/60 + 29.592/3600 = 48.858220
        dms = ((48, 1), (51, 1), (29592, 1000))
        result = extractor._gps_to_decimal(dms, "N")
        assert abs(result - 48.858220) < 0.00001

    def test_ref_case_insensitive(self, extractor):
        dms = ((10, 1), (0, 1), (0, 1))
        assert extractor._gps_to_decimal(dms, "s") < 0
        assert extractor._gps_to_decimal(dms, "w") < 0
        assert extractor._gps_to_decimal(dms, "n") > 0
        assert extractor._gps_to_decimal(dms, "e") > 0


# ===================================================================
# 4. Date parsing
# ===================================================================


class TestDateParsing:
    """Unit tests for _parse_exif_date with various formats."""

    def test_standard_format(self, extractor):
        result = extractor._parse_exif_date("2024:06:15 14:30:00")
        assert result == datetime(2024, 6, 15, 14, 30, 0)

    def test_missing_seconds(self, extractor):
        result = extractor._parse_exif_date("2024:06:15 14:30")
        assert result == datetime(2024, 6, 15, 14, 30, 0)

    def test_dash_separated(self, extractor):
        result = extractor._parse_exif_date("2024-06-15 14:30:00")
        assert result == datetime(2024, 6, 15, 14, 30, 0)

    def test_iso_with_t(self, extractor):
        result = extractor._parse_exif_date("2024-06-15T14:30:00")
        assert result == datetime(2024, 6, 15, 14, 30, 0)

    def test_date_only(self, extractor):
        result = extractor._parse_exif_date("2024:06:15")
        assert result == datetime(2024, 6, 15, 0, 0, 0)

    def test_all_zeros_returns_none(self, extractor):
        result = extractor._parse_exif_date("0000:00:00 00:00:00")
        assert result is None

    def test_empty_string_returns_none(self, extractor):
        result = extractor._parse_exif_date("")
        assert result is None

    def test_none_returns_none(self, extractor):
        result = extractor._parse_exif_date(None)
        assert result is None

    def test_whitespace_only_returns_none(self, extractor):
        result = extractor._parse_exif_date("   ")
        assert result is None

    def test_garbage_returns_none(self, extractor):
        result = extractor._parse_exif_date("not-a-date")
        assert result is None

    def test_extra_whitespace_stripped(self, extractor):
        result = extractor._parse_exif_date("  2024:06:15 14:30:00  ")
        assert result == datetime(2024, 6, 15, 14, 30, 0)

    def test_bytes_input(self, extractor):
        result = extractor._parse_exif_date(b"2024:06:15 14:30:00")
        assert result == datetime(2024, 6, 15, 14, 30, 0)


# ===================================================================
# 5. Corrupted EXIF
# ===================================================================


class TestCorruptedExif:
    """Extraction from a JPEG whose EXIF block is malformed."""

    def test_does_not_raise(self, extractor, corrupt_exif_jpg):
        # Must never raise — always return an ExifResult.
        result = extractor.extract(corrupt_exif_jpg)
        assert isinstance(result, ExifResult)

    def test_status_not_ok(self, extractor, corrupt_exif_jpg):
        result = extractor.extract(corrupt_exif_jpg)
        assert result.status in (ExifStatus.NONE, ExifStatus.PARTIAL, ExifStatus.ERROR)

    def test_dimensions_may_still_be_available(self, extractor, corrupt_exif_jpg):
        """Pillow can often decode the image even with bad EXIF."""
        result = extractor.extract(corrupt_exif_jpg)
        # Width/height come from Pillow, not EXIF, so may still work.
        if result.width is not None:
            assert result.width > 0
            assert result.height > 0


# ===================================================================
# 6. Non-image file
# ===================================================================


class TestNonImageFile:

    def test_text_file_returns_error(self, extractor, tmp_path):
        txt = tmp_path / "readme.txt"
        txt.write_text("Hello world")
        result = extractor.extract(str(txt))
        assert result.status == ExifStatus.ERROR
        assert result.error_message is not None

    def test_nonexistent_file_returns_error(self, extractor):
        result = extractor.extract("/nonexistent/photo.jpg")
        assert result.status == ExifStatus.ERROR
        assert result.error_message is not None


# ===================================================================
# 7. Partial EXIF data
# ===================================================================


class TestPartialExif:
    """JPEG with only some EXIF tags — status should be PARTIAL."""

    def test_date_only_no_camera(self, extractor, tmp_path):
        """Has DateTimeOriginal but no Make/Model → PARTIAL."""
        img = Image.new("RGB", (30, 30), color=(0, 0, 255))
        exif_dict = {
            "0th": {},
            "Exif": {
                piexif.ExifIFD.DateTimeOriginal: b"2023:01:01 12:00:00",
            },
            "GPS": {},
            "1st": {},
            "thumbnail": None,
        }
        exif_bytes = piexif.dump(exif_dict)
        out = str(tmp_path / "date_only.jpg")
        img.save(out, "JPEG", exif=exif_bytes)

        result = extractor.extract(out)
        assert result.status == ExifStatus.PARTIAL
        assert result.date_original == datetime(2023, 1, 1, 12, 0, 0)
        assert result.make is None

    def test_camera_only_no_date(self, extractor, tmp_path):
        """Has Make/Model but no dates → PARTIAL."""
        img = Image.new("RGB", (30, 30), color=(255, 0, 0))
        exif_dict = {
            "0th": {
                piexif.ImageIFD.Make: b"Nikon",
                piexif.ImageIFD.Model: b"Z9",
            },
            "Exif": {},
            "GPS": {},
            "1st": {},
            "thumbnail": None,
        }
        exif_bytes = piexif.dump(exif_dict)
        out = str(tmp_path / "camera_only.jpg")
        img.save(out, "JPEG", exif=exif_bytes)

        result = extractor.extract(out)
        assert result.status == ExifStatus.PARTIAL
        assert result.make == "Nikon"
        assert result.model == "Z9"
        assert result.date_original is None


# ===================================================================
# 8. Orientation values
# ===================================================================


class TestOrientation:

    @pytest.mark.parametrize("orient", [1, 3, 6, 8])
    def test_orientation_round_trip(self, extractor, tmp_path, orient):
        path = _build_jpeg_with_exif(tmp_path, orientation=orient)
        result = extractor.extract(path)
        assert result.orientation == orient


# ===================================================================
# 9. No thumbnail
# ===================================================================


class TestNoThumbnail:

    def test_no_thumbnail_flag(self, extractor, tmp_path):
        path = _build_jpeg_with_exif(tmp_path, include_thumbnail=False)
        result = extractor.extract(path)
        assert result.has_thumbnail is False


# ===================================================================
# 10. ExifTool availability check
# ===================================================================


class TestExifToolAvailability:

    def setup_method(self):
        from sortique.engine.metadata.exiftool_common import is_exiftool_available
        is_exiftool_available.cache_clear()

    def test_is_exiftool_available_returns_bool(self):
        from sortique.engine.metadata.exiftool_common import is_exiftool_available
        is_exiftool_available.cache_clear()
        result = ExifExtractor.is_exiftool_available()
        assert isinstance(result, bool)

    def test_exiftool_mock_not_available(self):
        from sortique.engine.metadata.exiftool_common import is_exiftool_available
        is_exiftool_available.cache_clear()
        with patch("sortique.engine.metadata.exiftool_common.shutil.which", return_value=None):
            assert ExifExtractor.is_exiftool_available() is False

    def test_exiftool_mock_available(self):
        from sortique.engine.metadata.exiftool_common import is_exiftool_available
        is_exiftool_available.cache_clear()
        with patch("sortique.engine.metadata.exiftool_common.shutil.which", return_value="/usr/bin/exiftool"):
            assert ExifExtractor.is_exiftool_available() is True


# ===================================================================
# 11. Internal helpers
# ===================================================================


class TestHelpers:

    def test_decode_bytes_none(self):
        assert _decode_bytes(None) is None

    def test_decode_bytes_empty(self):
        assert _decode_bytes(b"") is None

    def test_decode_bytes_normal(self):
        assert _decode_bytes(b"Canon") == "Canon"

    def test_decode_bytes_null_terminated(self):
        assert _decode_bytes(b"Canon\x00") == "Canon"

    def test_decode_bytes_str_passthrough(self):
        assert _decode_bytes("Canon") == "Canon"

    def test_decode_bytes_whitespace_only(self):
        assert _decode_bytes(b"   ") is None

    def test_rational_to_float_tuple(self):
        assert _rational_to_float((40, 1)) == 40.0
        assert _rational_to_float((4680, 100)) == 46.8

    def test_rational_to_float_zero_denominator(self):
        assert _rational_to_float((100, 0)) == 0.0

    def test_rational_to_float_plain_number(self):
        assert _rational_to_float(42.5) == 42.5
