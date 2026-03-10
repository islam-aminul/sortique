"""Video metadata extraction with multi-fallback chain."""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

from sortique.engine.metadata.exiftool_common import (
    is_exiftool_available as _is_exiftool_available,
    parse_exiftool_date as _parse_exiftool_date,
    run_exiftool as _run_exiftool,
)

# On Windows, prevent a visible cmd.exe window from flashing when
# launching subprocesses from a GUI application.
_SUBPROCESS_FLAGS: int = (
    subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
)


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class VideoMetadata:
    """Metadata extracted from a video file."""

    duration_seconds: float | None = None
    make: str | None = None
    model: str | None = None
    date: datetime | None = None
    width: int | None = None
    height: int | None = None
    duration_unknown: bool = False
    # True when GPS coordinates were found — strong signal for a camera recording.
    has_location: bool = False
    # Encoding software tag (e.g. "Lavf58.76.100", "HandBrake 1.6.1").
    encoder: str | None = None


# ---------------------------------------------------------------------------
# Encoding-software keyword filter
# ---------------------------------------------------------------------------

_ENCODER_KEYWORDS: frozenset[str] = frozenset({
    "lavf", "lavc", "ffmpeg", "handbrake", "x264", "x265",
    "libx264", "libx265", "mencoder", "nero", "divx", "xvid", "libxvid",
})


def _looks_like_encoder(value: str | None) -> bool:
    """Return True if *value* looks like an encoding library, not a camera brand."""
    if not value:
        return False
    lower = value.lower()
    return any(kw in lower for kw in _ENCODER_KEYWORDS)


# ---------------------------------------------------------------------------
# MP4 / MOV epoch  (seconds between 1904-01-01 and 1970-01-01)
# ---------------------------------------------------------------------------

_MP4_EPOCH_OFFSET = 2_082_844_800


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------

class VideoMetadataExtractor:
    """Video metadata extraction with multi-fallback chain.

    Fallback order:

    1. Binary parsing of MP4/MOV container atoms.
    2. ``ffprobe`` subprocess.
    3. ``exiftool`` subprocess (when installed).
    4. Return a result with ``duration_unknown=True``.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(self, filepath: str) -> VideoMetadata:
        """Extract video metadata.  Never raises."""
        try:
            result = self._extract_mp4_metadata(filepath)
            if result is not None:
                return result
        except Exception:
            pass

        try:
            result = self._extract_ffprobe(filepath)
            if result is not None:
                return result
        except Exception:
            pass

        try:
            result = self._extract_exiftool(filepath)
            if result is not None:
                return result
        except Exception:
            pass

        return VideoMetadata(duration_unknown=True)

    # ------------------------------------------------------------------
    # Tier 1 — MP4 / MOV binary parsing
    # ------------------------------------------------------------------

    def _extract_mp4_metadata(self, filepath: str) -> VideoMetadata | None:
        """Parse MP4/MOV container for metadata without external tools.

        Reads top-level atoms looking for *moov*.  Inside *moov* it reads
        *mvhd* (duration / timescale) and any *trak → tkhd* (width / height).
        Creation-time is taken from *mvhd*.
        """
        try:
            with open(filepath, "rb") as fh:
                # Quick sniff: first 12 bytes must contain 'ftyp' or 'moov'.
                header = fh.read(12)
                if len(header) < 12:
                    return None
                box_type = header[4:8]
                if box_type not in (b"ftyp", b"moov", b"wide", b"free"):
                    return None

                fh.seek(0, 2)
                file_size = fh.tell()
                fh.seek(0)

                moov_data = self._find_atom(fh, file_size, b"moov")
                if moov_data is None:
                    return None

                return self._parse_moov(moov_data)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Atom helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_atom(fh, end: int, target: bytes) -> bytes | None:
        """Walk top-level atoms and return the raw body of *target*."""
        while fh.tell() < end:
            pos = fh.tell()
            hdr = fh.read(8)
            if len(hdr) < 8:
                return None
            size = struct.unpack(">I", hdr[:4])[0]
            atom_type = hdr[4:8]

            if size == 1:
                # 64-bit extended size
                ext = fh.read(8)
                if len(ext) < 8:
                    return None
                size = struct.unpack(">Q", ext)[0]
                body_offset = 16
            elif size == 0:
                # Atom extends to EOF.
                size = end - pos
                body_offset = 8
            else:
                body_offset = 8

            body_size = size - body_offset
            if body_size < 0:
                return None

            if atom_type == target:
                data = fh.read(body_size)
                if len(data) < body_size:
                    return None
                return data

            # Skip to next atom.
            fh.seek(pos + size)

        return None

    def _parse_moov(self, moov: bytes) -> VideoMetadata:
        """Extract fields from *moov* atom body."""
        duration_seconds: float | None = None
        date: datetime | None = None
        width: int | None = None
        height: int | None = None
        make: str | None = None
        model: str | None = None

        offset = 0
        length = len(moov)
        while offset < length:
            if offset + 8 > length:
                break
            size = struct.unpack(">I", moov[offset : offset + 4])[0]
            atom_type = moov[offset + 4 : offset + 8]

            if size < 8:
                break

            body_start = offset + 8
            body_end = offset + size

            if atom_type == b"mvhd":
                duration_seconds, date = self._parse_mvhd(moov[body_start:body_end])

            elif atom_type == b"trak" and width is None:
                w, h = self._parse_trak_for_dimensions(moov[body_start:body_end])
                if w and h:
                    width, height = w, h

            elif atom_type == b"udta":
                m, mo = self._parse_udta(moov[body_start:body_end])
                if m:
                    make = m
                if mo:
                    model = mo

            offset = body_end

        if duration_seconds is None and date is None and width is None:
            return VideoMetadata(duration_unknown=True)

        return VideoMetadata(
            duration_seconds=duration_seconds,
            make=make,
            model=model,
            date=date,
            width=width,
            height=height,
        )

    # ------------------------------------------------------------------
    # Sub-atom parsers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_mvhd(data: bytes) -> tuple[float | None, datetime | None]:
        """Parse *mvhd* atom → (duration_seconds, creation_datetime)."""
        if len(data) < 4:
            return None, None

        version = data[0]

        if version == 0:
            # Version 0: 4-byte fields.
            if len(data) < 20:
                return None, None
            creation_time = struct.unpack(">I", data[4:8])[0]
            timescale = struct.unpack(">I", data[12:16])[0]
            duration = struct.unpack(">I", data[16:20])[0]
        elif version == 1:
            # Version 1: 8-byte fields.
            if len(data) < 36:
                return None, None
            creation_time = struct.unpack(">Q", data[4:12])[0]
            timescale = struct.unpack(">I", data[20:24])[0]
            duration = struct.unpack(">Q", data[24:32])[0]
        else:
            return None, None

        dur_secs: float | None = None
        if timescale > 0:
            dur_secs = round(duration / timescale, 3)

        dt: datetime | None = None
        if creation_time > _MP4_EPOCH_OFFSET:
            try:
                unix_ts = creation_time - _MP4_EPOCH_OFFSET
                dt = datetime.fromtimestamp(unix_ts, tz=timezone.utc)
            except (OSError, OverflowError, ValueError):
                pass

        return dur_secs, dt

    @staticmethod
    def _parse_trak_for_dimensions(trak: bytes) -> tuple[int | None, int | None]:
        """Walk *trak* children looking for *tkhd* to get width/height."""
        offset = 0
        length = len(trak)
        while offset < length:
            if offset + 8 > length:
                break
            size = struct.unpack(">I", trak[offset : offset + 4])[0]
            atom_type = trak[offset + 4 : offset + 8]
            if size < 8:
                break

            if atom_type == b"tkhd":
                body = trak[offset + 8 : offset + size]
                if len(body) < 4:
                    break
                version = body[0]
                if version == 0 and len(body) >= 84:
                    w_fixed = struct.unpack(">I", body[76:80])[0]
                    h_fixed = struct.unpack(">I", body[80:84])[0]
                elif version == 1 and len(body) >= 96:
                    w_fixed = struct.unpack(">I", body[88:92])[0]
                    h_fixed = struct.unpack(">I", body[92:96])[0]
                else:
                    break
                w = w_fixed >> 16
                h = h_fixed >> 16
                if w > 0 and h > 0:
                    return w, h

            offset += size

        return None, None

    @staticmethod
    def _parse_udta(udta: bytes) -> tuple[str | None, str | None]:
        """Best-effort parse of *udta* for make / model strings.

        Apple devices store ``@mak`` (make) and ``@mod`` (model) atoms
        inside a *meta* sub-atom.  This parser walks the bytes looking
        for those four-byte tags and extracts trailing UTF-8 text.
        """
        make: str | None = None
        model: str | None = None

        # Look for Apple metadata tags in raw bytes.
        for tag, attr in ((b"\xa9mak", "make"), (b"\xa9mod", "model")):
            idx = udta.find(tag)
            if idx < 0:
                continue
            # Atom: [4-byte size][4-byte type][body...]
            if idx + 8 > len(udta):
                continue
            atom_size = struct.unpack(">I", udta[idx : idx + 4])[0]
            body = udta[idx + 8 : idx + atom_size]
            # The body may contain a 'data' sub-atom; skip first 8 bytes
            # of data atom header if present, else try raw text.
            text = _extract_text(body)
            if text:
                if attr == "make":
                    make = text
                else:
                    model = text

        return make, model

    # ------------------------------------------------------------------
    # Tier 2 — ffprobe
    # ------------------------------------------------------------------

    def _extract_ffprobe(self, filepath: str) -> VideoMetadata | None:
        """Use ffprobe for metadata.  Returns ``None`` on any failure."""
        if not self.is_ffprobe_available():
            return None

        try:
            proc = subprocess.run(
                [
                    "ffprobe",
                    "-v", "quiet",
                    "-print_format", "json",
                    "-show_format",
                    "-show_streams",
                    filepath,
                ],
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=_SUBPROCESS_FLAGS,
            )
            if proc.returncode != 0:
                return None

            info = json.loads(proc.stdout)
        except Exception:
            return None

        return self._parse_ffprobe_json(info)

    @staticmethod
    def is_ffprobe_available() -> bool:
        """Return ``True`` if ``ffprobe`` is on *PATH*."""
        return shutil.which("ffprobe") is not None

    @staticmethod
    def _parse_ffprobe_json(info: dict) -> VideoMetadata:
        """Turn ffprobe JSON into a :class:`VideoMetadata`."""
        fmt = info.get("format", {})
        streams = info.get("streams", [])

        duration: float | None = None
        raw_dur = fmt.get("duration")
        if raw_dur is not None:
            try:
                duration = round(float(raw_dur), 3)
            except (ValueError, TypeError):
                pass

        width: int | None = None
        height: int | None = None
        video_stream_tags: dict = {}
        for s in streams:
            if s.get("codec_type") == "video":
                try:
                    width = int(s["width"])
                    height = int(s["height"])
                except (KeyError, ValueError, TypeError):
                    pass
                video_stream_tags = s.get("tags", {})
                break

        # --- make / model ---
        # Priority: format-level Apple tags → format-level generic → Android
        # manufacturer → stream-level tags (some cameras embed at stream level).
        fmt_tags = fmt.get("tags", {})
        make = (
            fmt_tags.get("make")
            or fmt_tags.get("com.apple.quicktime.make")
            or fmt_tags.get("com.android.manufacturer")
        )
        model = (
            fmt_tags.get("model")
            or fmt_tags.get("com.apple.quicktime.model")
            or fmt_tags.get("com.android.model")
        )
        # Fall back to stream-level tags (GoPro and some cameras use these).
        if not make:
            make = (
                video_stream_tags.get("make")
                or video_stream_tags.get("com.apple.quicktime.make")
                or video_stream_tags.get("com.android.manufacturer")
            )
        if not model:
            model = (
                video_stream_tags.get("model")
                or video_stream_tags.get("com.apple.quicktime.model")
                or video_stream_tags.get("com.android.model")
            )

        # Filter out encoding-library names that sometimes appear in make/model.
        if _looks_like_encoder(make) or _looks_like_encoder(model):
            make = None
            model = None

        # --- GPS / location ---
        # iPhone: com.apple.quicktime.location.ISO6709 or location tag.
        # Android: location tag in ISO 6709 format (e.g. "+37.3861-122.0839/").
        has_location = bool(
            fmt_tags.get("location")
            or fmt_tags.get("com.apple.quicktime.location.ISO6709")
            or fmt_tags.get("com.apple.quicktime.location")
        )

        # --- encoder ---
        encoder = fmt_tags.get("encoder") or video_stream_tags.get("encoder")
        if isinstance(encoder, str):
            encoder = encoder.strip() or None

        # --- date ---
        date: datetime | None = None
        date_str = (
            fmt_tags.get("creation_time")
            or fmt_tags.get("date")
            or fmt_tags.get("com.apple.quicktime.creationdate")
        )
        if date_str:
            for date_fmt in (
                "%Y-%m-%dT%H:%M:%S.%fZ",
                "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d",
            ):
                try:
                    date = datetime.strptime(date_str, date_fmt).replace(
                        tzinfo=timezone.utc,
                    )
                    break
                except ValueError:
                    continue

        return VideoMetadata(
            duration_seconds=duration,
            make=make,
            model=model,
            date=date,
            width=width,
            height=height,
            duration_unknown=(duration is None),
            has_location=has_location,
            encoder=encoder,
        )

    # ------------------------------------------------------------------
    # Tier 3 — ExifTool subprocess
    # ------------------------------------------------------------------

    def _extract_exiftool(self, filepath: str) -> VideoMetadata | None:
        """Use ExifTool for video metadata.  Returns ``None`` on any failure."""
        d = _run_exiftool(filepath)
        if d is None:
            return None

        return self._parse_exiftool_json(d)

    @staticmethod
    def is_exiftool_available() -> bool:
        """Return ``True`` if ``exiftool`` is on *PATH*."""
        return _is_exiftool_available()

    @staticmethod
    def _parse_exiftool_json(d: dict) -> VideoMetadata:
        """Turn ExifTool JSON dict into a :class:`VideoMetadata`."""
        # Duration: ExifTool reports Duration in seconds (with -n flag).
        duration: float | None = None
        raw_dur = d.get("Duration")
        if raw_dur is not None:
            try:
                duration = round(float(raw_dur), 3)
            except (ValueError, TypeError):
                pass

        # Dimensions
        width: int | None = None
        height: int | None = None
        raw_w = d.get("ImageWidth")
        raw_h = d.get("ImageHeight")
        if raw_w is not None and raw_h is not None:
            try:
                width = int(raw_w)
                height = int(raw_h)
            except (ValueError, TypeError):
                pass

        # Make / Model
        make = d.get("Make")
        model = d.get("Model")
        if isinstance(make, str):
            make = make.strip() or None
        else:
            make = None
        if isinstance(model, str):
            model = model.strip() or None
        else:
            model = None

        # Filter out encoding-library names that sometimes appear in make/model.
        if _looks_like_encoder(make) or _looks_like_encoder(model):
            make = None
            model = None

        # GPS — ExifTool emits numeric degrees when run with -n.
        has_location = (
            d.get("GPSLatitude") is not None
            or d.get("GPSLongitude") is not None
        )

        # Encoder / Software tag.
        encoder_raw = d.get("Software") or d.get("Encoder")
        encoder = encoder_raw.strip() if isinstance(encoder_raw, str) else None
        encoder = encoder or None

        # Creation date: ExifTool uses several tag names for video dates.
        date: datetime | None = None
        for key in ("DateTimeOriginal", "CreateDate",
                     "MediaCreateDate", "TrackCreateDate"):
            date = _parse_exiftool_date(d.get(key))
            if date is not None:
                break

        return VideoMetadata(
            duration_seconds=duration,
            make=make,
            model=model,
            date=date,
            width=width,
            height=height,
            duration_unknown=(duration is None),
            has_location=has_location,
            encoder=encoder,
        )


# ---------------------------------------------------------------------------
# Module-level helper
# ---------------------------------------------------------------------------

def _extract_text(body: bytes) -> str | None:
    """Try to pull a UTF-8 string from an atom body, skipping known headers."""
    if not body:
        return None

    # Apple 'data' sub-atom: [size:4][type:4='data'][flags:8][text...]
    if len(body) > 16 and body[4:8] == b"data":
        text_bytes = body[16:]
    else:
        text_bytes = body

    try:
        text = text_bytes.decode("utf-8", errors="ignore").strip("\x00").strip()
        return text if text else None
    except Exception:
        return None
