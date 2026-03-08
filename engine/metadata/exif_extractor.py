"""Two-tier EXIF extraction: Pillow/piexif primary, ExifTool fallback."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import piexif
from PIL import Image

from sortique.constants import ExifStatus
from sortique.engine.metadata.exiftool_common import (
    is_exiftool_available as _is_exiftool_available,
    run_exiftool as _run_exiftool,
)


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class ExifResult:
    """Outcome of EXIF extraction for a single file."""

    status: ExifStatus = ExifStatus.NONE
    make: str | None = None
    model: str | None = None
    software: str | None = None
    date_original: datetime | None = None
    date_digitized: datetime | None = None
    date_modified: datetime | None = None
    gps_lat: float | None = None
    gps_lon: float | None = None
    orientation: int | None = None
    width: int | None = None
    height: int | None = None
    timezone_offset: str | None = None
    has_thumbnail: bool = False
    error_message: str | None = None


# ---------------------------------------------------------------------------
# Tag constants (raw IDs for maximum compatibility)
# ---------------------------------------------------------------------------

# IFD0
_TAG_MAKE = 0x010F
_TAG_MODEL = 0x0110
_TAG_ORIENTATION = 0x0112
_TAG_SOFTWARE = 0x0131
_TAG_DATETIME = 0x0132

# EXIF IFD
_TAG_DATETIME_ORIGINAL = 0x9003
_TAG_DATETIME_DIGITIZED = 0x9004
_TAG_OFFSET_TIME_ORIGINAL = 0x9011
_TAG_PIXEL_X = 0xA002
_TAG_PIXEL_Y = 0xA003

# EXIF IFD pointer (for Pillow get_ifd)
_IFD_EXIF = 0x8769
_IFD_GPS = 0x8825

# GPS IFD (piexif constants)
_GPS_LAT_REF = piexif.GPSIFD.GPSLatitudeRef      # 1
_GPS_LAT = piexif.GPSIFD.GPSLatitude              # 2
_GPS_LON_REF = piexif.GPSIFD.GPSLongitudeRef      # 3
_GPS_LON = piexif.GPSIFD.GPSLongitude             # 4


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------

class ExifExtractor:
    """Two-tier EXIF extraction: Pillow/piexif primary, ExifTool fallback.

    * Tier 1 — ``piexif.load()`` gives structured IFD access to JPEG/TIFF.
      If piexif cannot parse the file, fall back to Pillow's ``getexif()``.
    * Tier 2 — ``exiftool -json -n`` subprocess (when installed).

    Public methods never raise.  The result's :attr:`ExifResult.status`
    indicates the outcome.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(self, filepath: str) -> ExifResult:
        """Extract EXIF data from *filepath*.

        Strategy:

        1. Try Pillow + piexif first (fast, pure Python).
        2. If that fails or returns partial data, try ExifTool subprocess
           (if available on the system ``PATH``).
        3. If ExifTool is not installed, return whatever Pillow got with
           the appropriate status.
        4. Never raise exceptions — always return an :class:`ExifResult`
           with ``status`` indicating the outcome.
        """
        result = self._extract_pillow(filepath)

        if result.status == ExifStatus.OK:
            return result

        # Pillow gave incomplete or no data — try ExifTool as fallback.
        if self.is_exiftool_available():
            et_result = self._extract_exiftool(filepath)
            if _status_rank(et_result.status) < _status_rank(result.status):
                return et_result

        return result

    # ------------------------------------------------------------------
    # Tier 1 — Pillow + piexif
    # ------------------------------------------------------------------

    def _extract_pillow(self, filepath: str) -> ExifResult:
        """Extract using Pillow for image dimensions and piexif for tags.

        Falls back to Pillow's ``getexif()`` if piexif cannot parse the
        file (e.g. non-JPEG/TIFF images).
        """
        # -- open image for width / height ---
        try:
            img = Image.open(filepath)
            width, height = img.size
        except Exception as exc:
            return ExifResult(
                status=ExifStatus.ERROR,
                error_message=f"Cannot open image: {exc}",
            )

        # Accumulate extracted values.
        make = model = software = None
        date_original = date_digitized = date_modified = None
        gps_lat: float | None = None
        gps_lon: float | None = None
        orientation: int | None = None
        tz_offset: str | None = None
        has_thumbnail = False

        # -- attempt piexif (structured IFD access) ---
        piexif_ok = False
        try:
            exif_dict = piexif.load(filepath)
            piexif_ok = True
        except Exception:
            exif_dict = None

        if piexif_ok and exif_dict is not None:
            ifd0 = exif_dict.get("0th", {})
            exif_ifd = exif_dict.get("Exif", {})
            gps_ifd = exif_dict.get("GPS", {})

            make = _decode_bytes(ifd0.get(_TAG_MAKE))
            model = _decode_bytes(ifd0.get(_TAG_MODEL))
            software = _decode_bytes(ifd0.get(_TAG_SOFTWARE))
            orientation = ifd0.get(_TAG_ORIENTATION)
            date_modified = self._parse_exif_date(
                _decode_bytes(ifd0.get(_TAG_DATETIME)),
            )

            date_original = self._parse_exif_date(
                _decode_bytes(exif_ifd.get(_TAG_DATETIME_ORIGINAL)),
            )
            date_digitized = self._parse_exif_date(
                _decode_bytes(exif_ifd.get(_TAG_DATETIME_DIGITIZED)),
            )
            tz_offset = _decode_bytes(exif_ifd.get(_TAG_OFFSET_TIME_ORIGINAL))

            # GPS
            try:
                if _GPS_LAT in gps_ifd and _GPS_LAT_REF in gps_ifd:
                    gps_lat = self._gps_to_decimal(
                        gps_ifd[_GPS_LAT],
                        _decode_bytes(gps_ifd[_GPS_LAT_REF]) or "",
                    )
                if _GPS_LON in gps_ifd and _GPS_LON_REF in gps_ifd:
                    gps_lon = self._gps_to_decimal(
                        gps_ifd[_GPS_LON],
                        _decode_bytes(gps_ifd[_GPS_LON_REF]) or "",
                    )
            except (TypeError, ValueError, ZeroDivisionError):
                pass

            has_thumbnail = bool(exif_dict.get("thumbnail"))
        else:
            # -- fallback: Pillow getexif() ---
            try:
                pil_exif = img.getexif()
                if pil_exif:
                    make = pil_exif.get(_TAG_MAKE)
                    model = pil_exif.get(_TAG_MODEL)
                    software = pil_exif.get(_TAG_SOFTWARE)
                    orientation = pil_exif.get(_TAG_ORIENTATION)
                    date_modified = self._parse_exif_date(
                        pil_exif.get(_TAG_DATETIME),
                    )

                    exif_sub = pil_exif.get_ifd(_IFD_EXIF)
                    if exif_sub:
                        date_original = self._parse_exif_date(
                            exif_sub.get(_TAG_DATETIME_ORIGINAL),
                        )
                        date_digitized = self._parse_exif_date(
                            exif_sub.get(_TAG_DATETIME_DIGITIZED),
                        )
                        tz_offset = exif_sub.get(_TAG_OFFSET_TIME_ORIGINAL)

                    gps_sub = pil_exif.get_ifd(_IFD_GPS)
                    if gps_sub:
                        try:
                            lat = gps_sub.get(_GPS_LAT)
                            lat_ref = gps_sub.get(_GPS_LAT_REF)
                            lon = gps_sub.get(_GPS_LON)
                            lon_ref = gps_sub.get(_GPS_LON_REF)
                            if lat and lat_ref:
                                gps_lat = self._gps_to_decimal(lat, lat_ref)
                            if lon and lon_ref:
                                gps_lon = self._gps_to_decimal(lon, lon_ref)
                        except (TypeError, ValueError, ZeroDivisionError):
                            pass
            except Exception:
                pass

        # -- determine status ---
        has_any = any([
            make, model, software,
            date_original, date_digitized, date_modified,
            gps_lat is not None,
        ])

        if not has_any:
            status = ExifStatus.NONE
        elif date_original is not None and (make is not None or model is not None):
            status = ExifStatus.OK
        else:
            status = ExifStatus.PARTIAL

        return ExifResult(
            status=status,
            make=make,
            model=model,
            software=software,
            date_original=date_original,
            date_digitized=date_digitized,
            date_modified=date_modified,
            gps_lat=gps_lat,
            gps_lon=gps_lon,
            orientation=orientation,
            width=width,
            height=height,
            timezone_offset=tz_offset,
            has_thumbnail=has_thumbnail,
            error_message=None,
        )

    # ------------------------------------------------------------------
    # Tier 2 — ExifTool subprocess
    # ------------------------------------------------------------------

    def _extract_exiftool(self, filepath: str) -> ExifResult:
        """Fallback: call ``exiftool -json -n <filepath>`` via shared utility.

        Returns an :class:`ExifResult` with ``status=ERROR`` if exiftool
        is not available or the subprocess fails.
        """
        d = _run_exiftool(filepath)
        if d is None:
            return ExifResult(
                status=ExifStatus.ERROR,
                error_message="exiftool not available or failed",
            )

        try:
            make = d.get("Make")
            model = d.get("Model")
            software = d.get("Software")
            date_original = self._parse_exif_date(d.get("DateTimeOriginal"))
            date_digitized = self._parse_exif_date(d.get("CreateDate"))
            date_modified = self._parse_exif_date(d.get("ModifyDate"))
            orientation = d.get("Orientation")
            width = d.get("ImageWidth")
            height = d.get("ImageHeight")
            tz_offset = d.get("OffsetTimeOriginal")
            has_thumbnail = "ThumbnailImage" in d

            # With -n, exiftool outputs signed decimal GPS coordinates.
            gps_lat = d.get("GPSLatitude")
            gps_lon = d.get("GPSLongitude")
            if isinstance(gps_lat, (int, float)):
                gps_lat = float(gps_lat)
            else:
                gps_lat = None
            if isinstance(gps_lon, (int, float)):
                gps_lon = float(gps_lon)
            else:
                gps_lon = None

            # -- determine status ---
            has_any = any([
                make, model, software,
                date_original, date_digitized, date_modified,
                gps_lat is not None,
            ])

            if not has_any:
                status = ExifStatus.NONE
            elif (
                date_original is not None
                and (make is not None or model is not None)
            ):
                status = ExifStatus.OK
            else:
                status = ExifStatus.PARTIAL

            return ExifResult(
                status=status,
                make=make,
                model=model,
                software=software,
                date_original=date_original,
                date_digitized=date_digitized,
                date_modified=date_modified,
                gps_lat=gps_lat,
                gps_lon=gps_lon,
                orientation=orientation,
                width=width,
                height=height,
                timezone_offset=tz_offset,
                has_thumbnail=has_thumbnail,
                error_message=None,
            )
        except Exception as exc:
            return ExifResult(
                status=ExifStatus.ERROR,
                error_message=f"exiftool failed: {exc}",
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _parse_exif_date(self, date_str: str | None) -> datetime | None:
        """Parse EXIF date string ``'YYYY:MM:DD HH:MM:SS'`` to *datetime*.

        Handles common malformations: extra whitespace, missing seconds,
        dash-separated dates, ISO-style ``T`` separator, and the
        all-zeros sentinel ``0000:00:00 00:00:00``.
        """
        if date_str is None:
            return None

        if isinstance(date_str, bytes):
            date_str = date_str.decode("utf-8", errors="replace")

        date_str = date_str.strip()
        if not date_str or date_str.startswith("0000"):
            return None

        formats = (
            "%Y:%m:%d %H:%M:%S",     # standard EXIF
            "%Y:%m:%d %H:%M",         # missing seconds
            "%Y-%m-%d %H:%M:%S",      # dash-separated
            "%Y-%m-%dT%H:%M:%S",      # ISO with T
            "%Y:%m:%d",               # date only
        )
        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        return None

    def _gps_to_decimal(self, dms: tuple, ref: str) -> float:
        """Convert GPS DMS ``((d_n,d_d),(m_n,m_d),(s_n,s_d))`` + ref to decimal.

        *ref* is ``'N'``, ``'S'``, ``'E'``, or ``'W'``.
        Handles both piexif rational tuples ``(num, den)`` and Pillow
        ``IFDRational`` objects that support ``float()``.
        """
        degrees = _rational_to_float(dms[0])
        minutes = _rational_to_float(dms[1])
        seconds = _rational_to_float(dms[2])

        decimal = degrees + minutes / 60.0 + seconds / 3600.0

        if ref.upper() in ("S", "W"):
            decimal = -decimal

        return decimal

    @staticmethod
    def is_exiftool_available() -> bool:
        """Return ``True`` when ``exiftool`` is found on the system PATH."""
        return _is_exiftool_available()


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _decode_bytes(value: object) -> str | None:
    """Decode a piexif tag value that may be ``bytes``, ``str``, or ``None``."""
    if value is None:
        return None
    if isinstance(value, bytes):
        decoded = value.decode("utf-8", errors="replace").rstrip("\x00").strip()
        return decoded if decoded else None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    return None


def _rational_to_float(val: object) -> float:
    """Convert a piexif rational ``(num, den)`` or Pillow IFDRational to float."""
    if isinstance(val, tuple) and len(val) == 2:
        num, den = val
        return num / den if den != 0 else 0.0
    return float(val)


def _status_rank(status: ExifStatus) -> int:
    """Numeric rank for status comparison (lower is better)."""
    return {
        ExifStatus.OK: 0,
        ExifStatus.PARTIAL: 1,
        ExifStatus.NONE: 2,
        ExifStatus.ERROR: 3,
    }.get(status, 4)
