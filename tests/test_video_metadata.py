"""Tests for sortique.engine.metadata.video_metadata."""

from __future__ import annotations

import json
import struct
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sortique.engine.metadata.video_metadata import (
    VideoMetadata,
    VideoMetadataExtractor,
    _MP4_EPOCH_OFFSET,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def extractor():
    return VideoMetadataExtractor()


# ---------------------------------------------------------------------------
# MP4 builder helpers
# ---------------------------------------------------------------------------

def _box(box_type: bytes, payload: bytes) -> bytes:
    """Build a single ISO base-media-file-format box (atom)."""
    size = 8 + len(payload)
    return struct.pack(">I", size) + box_type + payload


def _build_ftyp() -> bytes:
    """Minimal *ftyp* box."""
    return _box(b"ftyp", b"isom" + b"\x00" * 4 + b"isom" + b"mp41")


def _build_mvhd(
    *,
    timescale: int = 1000,
    duration: int = 5000,
    creation_time: int = 0,
    version: int = 0,
) -> bytes:
    """Build a version-0 or version-1 *mvhd* atom."""
    if version == 0:
        body = (
            struct.pack(">B", 0)                    # version
            + b"\x00" * 3                            # flags
            + struct.pack(">I", creation_time)       # creation_time
            + struct.pack(">I", 0)                   # modification_time
            + struct.pack(">I", timescale)           # timescale
            + struct.pack(">I", duration)            # duration
            + b"\x00" * 80                           # remainder (rate, volume, matrix, etc.)
        )
    else:
        body = (
            struct.pack(">B", 1)                    # version
            + b"\x00" * 3                            # flags
            + struct.pack(">Q", creation_time)       # creation_time
            + struct.pack(">Q", 0)                   # modification_time
            + struct.pack(">I", timescale)           # timescale
            + struct.pack(">Q", duration)            # duration
            + b"\x00" * 80                           # remainder
        )
    return _box(b"mvhd", body)


def _build_tkhd(width: int, height: int, version: int = 0) -> bytes:
    """Build a *tkhd* atom with fixed-point 16.16 width/height."""
    w_fixed = width << 16
    h_fixed = height << 16
    if version == 0:
        body = (
            struct.pack(">B", 0)                    # version
            + b"\x00" * 3                            # flags
            + b"\x00" * 72                           # creation_time…matrix
            + struct.pack(">I", w_fixed)             # width  (16.16)
            + struct.pack(">I", h_fixed)             # height (16.16)
        )
    else:
        body = (
            struct.pack(">B", 1)                    # version
            + b"\x00" * 3                            # flags
            + b"\x00" * 84                           # creation_time…matrix (larger for v1)
            + struct.pack(">I", w_fixed)
            + struct.pack(">I", h_fixed)
        )
    return _box(b"tkhd", body)


def _build_trak(width: int, height: int) -> bytes:
    """Build a *trak* atom containing a *tkhd*."""
    return _box(b"trak", _build_tkhd(width, height))


def _build_minimal_mp4(
    *,
    timescale: int = 1000,
    duration: int = 5000,
    width: int = 1920,
    height: int = 1080,
    creation_time: int = 0,
) -> bytes:
    """Return a minimal MP4 byte-string with ftyp + moov(mvhd + trak)."""
    moov_body = _build_mvhd(
        timescale=timescale,
        duration=duration,
        creation_time=creation_time,
    ) + _build_trak(width, height)
    return _build_ftyp() + _box(b"moov", moov_body)


def _write_mp4(path: Path, **kwargs) -> str:
    """Write a minimal MP4 and return the path as a string."""
    data = _build_minimal_mp4(**kwargs)
    path.write_bytes(data)
    return str(path)


# ===========================================================================
# 1. VideoMetadata dataclass
# ===========================================================================

class TestVideoMetadataDataclass:

    def test_defaults(self):
        m = VideoMetadata()
        assert m.duration_seconds is None
        assert m.make is None
        assert m.model is None
        assert m.date is None
        assert m.width is None
        assert m.height is None
        assert m.duration_unknown is False

    def test_duration_unknown_flag(self):
        m = VideoMetadata(duration_unknown=True)
        assert m.duration_unknown is True


# ===========================================================================
# 2. MP4 binary parsing
# ===========================================================================

class TestMp4Parsing:

    def test_duration_and_dimensions(self, extractor, tmp_path):
        f = _write_mp4(tmp_path / "clip.mp4", duration=5000, timescale=1000,
                        width=1920, height=1080)
        result = extractor.extract(f)
        assert result.duration_seconds == 5.0
        assert result.width == 1920
        assert result.height == 1080
        assert result.duration_unknown is False

    def test_fractional_duration(self, extractor, tmp_path):
        f = _write_mp4(tmp_path / "clip.mp4", duration=7500, timescale=1000)
        result = extractor.extract(f)
        assert result.duration_seconds == 7.5

    def test_creation_time(self, extractor, tmp_path):
        # 2024-01-15 12:00:00 UTC  →  Unix timestamp 1705320000
        unix_ts = int(datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc).timestamp())
        mp4_time = unix_ts + _MP4_EPOCH_OFFSET
        f = _write_mp4(tmp_path / "dated.mp4", creation_time=mp4_time)
        result = extractor.extract(f)
        assert result.date is not None
        assert result.date.year == 2024
        assert result.date.month == 1
        assert result.date.day == 15

    def test_zero_creation_time_yields_none(self, extractor, tmp_path):
        f = _write_mp4(tmp_path / "nodate.mp4", creation_time=0)
        result = extractor.extract(f)
        assert result.date is None

    def test_custom_resolution(self, extractor, tmp_path):
        f = _write_mp4(tmp_path / "4k.mp4", width=3840, height=2160)
        result = extractor.extract(f)
        assert result.width == 3840
        assert result.height == 2160

    def test_non_mp4_file_returns_fallback(self, extractor, tmp_path):
        f = tmp_path / "readme.txt"
        f.write_text("hello world")
        result = extractor.extract(str(f))
        assert result.duration_unknown is True

    def test_empty_file(self, extractor, tmp_path):
        f = tmp_path / "empty.mp4"
        f.write_bytes(b"")
        result = extractor.extract(str(f))
        assert result.duration_unknown is True

    def test_truncated_mp4(self, extractor, tmp_path):
        """A file that starts like an MP4 but is truncated mid-moov."""
        data = _build_minimal_mp4()
        f = tmp_path / "trunc.mp4"
        f.write_bytes(data[:30])
        result = extractor.extract(str(f))
        assert result.duration_unknown is True

    def test_missing_file(self, extractor):
        result = extractor.extract("/nonexistent/video.mp4")
        assert result.duration_unknown is True


# ===========================================================================
# 3. ffprobe fallback
# ===========================================================================

class TestFfprobeFallback:

    def _mock_ffprobe_json(self, *, duration="10.5", width=1280, height=720,
                            creation_time=None, make=None, model=None):
        """Build a dict that mirrors ffprobe JSON output."""
        tags = {}
        if creation_time:
            tags["creation_time"] = creation_time
        if make:
            tags["com.apple.quicktime.make"] = make
        if model:
            tags["com.apple.quicktime.model"] = model
        return {
            "format": {
                "duration": duration,
                "tags": tags,
            },
            "streams": [
                {
                    "codec_type": "video",
                    "width": width,
                    "height": height,
                }
            ],
        }

    def test_ffprobe_parses_duration_and_dimensions(self, extractor):
        info = self._mock_ffprobe_json(duration="12.345", width=640, height=480)
        result = extractor._parse_ffprobe_json(info)
        assert result.duration_seconds == 12.345
        assert result.width == 640
        assert result.height == 480

    def test_ffprobe_parses_creation_time(self, extractor):
        info = self._mock_ffprobe_json(creation_time="2024-06-15T10:30:00.000000Z")
        result = extractor._parse_ffprobe_json(info)
        assert result.date is not None
        assert result.date.year == 2024
        assert result.date.month == 6

    def test_ffprobe_parses_make_model(self, extractor):
        info = self._mock_ffprobe_json(make="Apple", model="iPhone 15 Pro")
        result = extractor._parse_ffprobe_json(info)
        assert result.make == "Apple"
        assert result.model == "iPhone 15 Pro"

    def test_ffprobe_missing_duration(self, extractor):
        info = {"format": {}, "streams": []}
        result = extractor._parse_ffprobe_json(info)
        assert result.duration_seconds is None
        assert result.duration_unknown is True

    def test_ffprobe_no_video_stream(self, extractor):
        info = self._mock_ffprobe_json()
        info["streams"] = [{"codec_type": "audio"}]
        result = extractor._parse_ffprobe_json(info)
        assert result.width is None
        assert result.height is None

    @patch("sortique.engine.metadata.video_metadata.shutil.which", return_value=None)
    def test_ffprobe_not_available(self, mock_which, extractor):
        assert extractor.is_ffprobe_available() is False
        result = extractor._extract_ffprobe("anything.mp4")
        assert result is None

    @patch("sortique.engine.metadata.video_metadata.shutil.which", return_value="/usr/bin/ffprobe")
    @patch("sortique.engine.metadata.video_metadata.subprocess.run")
    def test_ffprobe_subprocess_failure(self, mock_run, mock_which, extractor):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        result = extractor._extract_ffprobe("bad.mp4")
        assert result is None


# ===========================================================================
# 4. Full fallback chain integration
# ===========================================================================

class TestFallbackChain:

    def test_mp4_success_skips_ffprobe(self, extractor, tmp_path):
        f = _write_mp4(tmp_path / "good.mp4")
        with patch.object(extractor, "_extract_ffprobe") as mock_ff:
            result = extractor.extract(f)
            mock_ff.assert_not_called()
        assert result.duration_seconds is not None

    def test_non_mp4_tries_ffprobe(self, extractor, tmp_path):
        f = tmp_path / "clip.avi"
        f.write_bytes(b"RIFF" + b"\x00" * 100)
        with patch.object(extractor, "_extract_ffprobe", return_value=VideoMetadata(duration_seconds=8.0)) as mock_ff:
            result = extractor.extract(str(f))
            mock_ff.assert_called_once()
        assert result.duration_seconds == 8.0

    def test_all_fail_returns_duration_unknown(self, extractor, tmp_path):
        f = tmp_path / "garbage.bin"
        f.write_bytes(b"\x00" * 50)
        with patch.object(extractor, "_extract_ffprobe", return_value=None):
            result = extractor.extract(str(f))
        assert result.duration_unknown is True

    def test_extract_never_raises(self, extractor, tmp_path):
        """Even pathological input must not raise."""
        f = tmp_path / "bad.mp4"
        f.write_bytes(b"\xff" * 200)
        result = extractor.extract(str(f))
        assert isinstance(result, VideoMetadata)


# ===========================================================================
# 5. is_ffprobe_available
# ===========================================================================

class TestFfprobeAvailability:

    @patch("sortique.engine.metadata.video_metadata.shutil.which", return_value="/usr/bin/ffprobe")
    def test_available(self, mock_which):
        assert VideoMetadataExtractor.is_ffprobe_available() is True

    @patch("sortique.engine.metadata.video_metadata.shutil.which", return_value=None)
    def test_not_available(self, mock_which):
        assert VideoMetadataExtractor.is_ffprobe_available() is False
