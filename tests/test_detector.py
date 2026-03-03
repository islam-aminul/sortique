"""Tests for sortique.engine.detector.ContentDetector."""

from __future__ import annotations

import os
import struct
from pathlib import Path

import pytest

from sortique.constants import FileType
from sortique.engine.detector import ContentDetector

_FIXTURES = Path(__file__).resolve().parent / "fixtures"

detector = ContentDetector()


# ======================================================================
# Magic-byte detection (real fixture files)
# ======================================================================

class TestMagicByteDetection:
    """Each fixture file must be identified by its binary header."""

    def test_jpeg(self) -> None:
        mime, ft = detector.detect(str(_FIXTURES / "sample.jpg"))
        assert mime == "image/jpeg"
        assert ft == FileType.IMAGE

    def test_png(self) -> None:
        mime, ft = detector.detect(str(_FIXTURES / "sample.png"))
        assert mime == "image/png"
        assert ft == FileType.IMAGE

    def test_gif(self) -> None:
        mime, ft = detector.detect(str(_FIXTURES / "sample.gif"))
        assert mime == "image/gif"
        assert ft == FileType.IMAGE

    def test_bmp(self) -> None:
        mime, ft = detector.detect(str(_FIXTURES / "sample.bmp"))
        assert mime == "image/bmp"
        assert ft == FileType.IMAGE

    def test_tiff(self) -> None:
        mime, ft = detector.detect(str(_FIXTURES / "sample.tif"))
        assert mime == "image/tiff"
        assert ft == FileType.IMAGE

    def test_pdf(self) -> None:
        mime, ft = detector.detect(str(_FIXTURES / "sample.pdf"))
        assert mime == "application/pdf"
        assert ft == FileType.DOCUMENT

    def test_flac(self) -> None:
        mime, ft = detector.detect(str(_FIXTURES / "sample.flac"))
        assert mime == "audio/flac"
        assert ft == FileType.AUDIO

    def test_ogg(self) -> None:
        mime, ft = detector.detect(str(_FIXTURES / "sample.ogg"))
        assert mime == "audio/ogg"
        assert ft == FileType.AUDIO

    def test_mp3_id3(self) -> None:
        mime, ft = detector.detect(str(_FIXTURES / "sample.mp3"))
        assert mime == "audio/mpeg"
        assert ft == FileType.AUDIO

    def test_mkv_webm(self) -> None:
        mime, ft = detector.detect(str(_FIXTURES / "sample.mkv"))
        assert ft == FileType.VIDEO


# ======================================================================
# RIFF disambiguation
# ======================================================================

class TestRiffDisambiguation:
    def test_webp(self) -> None:
        mime, ft = detector.detect(str(_FIXTURES / "sample.webp"))
        assert mime == "image/webp"
        assert ft == FileType.IMAGE

    def test_avi(self) -> None:
        mime, ft = detector.detect(str(_FIXTURES / "sample.avi"))
        assert mime == "video/x-msvideo"
        assert ft == FileType.VIDEO

    def test_wav(self) -> None:
        mime, ft = detector.detect(str(_FIXTURES / "sample.wav"))
        assert mime == "audio/wav"
        assert ft == FileType.AUDIO

    def test_riff_unknown_fourcc(self, tmp_path: Path) -> None:
        """A RIFF container with unrecognised sub-type falls back to extension."""
        f = tmp_path / "weird.avi"
        payload = b"XXXX" + b"\x00" * 8
        f.write_bytes(b"RIFF" + struct.pack("<I", len(payload)) + payload)
        mime, ft = detector.detect(str(f))
        # Extension fallback picks up .avi
        assert ft == FileType.VIDEO


# ======================================================================
# ftyp (ISO BMFF) disambiguation
# ======================================================================

class TestFtypDisambiguation:
    def test_mp4_isom(self) -> None:
        mime, ft = detector.detect(str(_FIXTURES / "sample.mp4"))
        assert mime == "video/mp4"
        assert ft == FileType.VIDEO

    def test_mov_qt(self) -> None:
        mime, ft = detector.detect(str(_FIXTURES / "sample.mov"))
        assert mime == "video/quicktime"
        assert ft == FileType.VIDEO

    def test_heic(self) -> None:
        mime, ft = detector.detect(str(_FIXTURES / "sample.heic"))
        assert mime == "image/heic"
        assert ft == FileType.IMAGE

    def test_avif(self) -> None:
        mime, ft = detector.detect(str(_FIXTURES / "sample.avif"))
        assert mime == "image/avif"
        assert ft == FileType.IMAGE

    def test_m4a_brand(self, tmp_path: Path) -> None:
        f = tmp_path / "audio.m4a"
        f.write_bytes(
            struct.pack(">I", 20) + b"ftyp" + b"M4A " + struct.pack(">I", 0) + b"M4A "
        )
        mime, ft = detector.detect(str(f))
        assert mime == "audio/mp4"
        assert ft == FileType.AUDIO

    def test_ftyp_unknown_brand_defaults_to_mp4(self, tmp_path: Path) -> None:
        f = tmp_path / "mystery.mp4"
        f.write_bytes(
            struct.pack(">I", 20) + b"ftyp" + b"ZZZZ" + struct.pack(">I", 0) + b"ZZZZ"
        )
        mime, ft = detector.detect(str(f))
        assert mime == "video/mp4"
        assert ft == FileType.VIDEO


# ======================================================================
# Extension fallback
# ======================================================================

class TestExtensionFallback:
    """When magic bytes don't match, the extension should still work."""

    def test_cr2_raw(self, tmp_path: Path) -> None:
        f = tmp_path / "photo.cr2"
        f.write_bytes(b"\x00" * 32)  # no matching magic
        mime, ft = detector.detect(str(f))
        assert ft == FileType.IMAGE
        assert "canon" in mime

    def test_nef_raw(self, tmp_path: Path) -> None:
        f = tmp_path / "photo.nef"
        f.write_bytes(b"\x00" * 32)
        _, ft = detector.detect(str(f))
        assert ft == FileType.IMAGE

    def test_docx(self, tmp_path: Path) -> None:
        # DOCX is a ZIP internally, but if the magic doesn't match PK header
        f = tmp_path / "report.docx"
        f.write_bytes(b"\x00" * 32)
        _, ft = detector.detect(str(f))
        assert ft == FileType.DOCUMENT

    def test_srt_sidecar(self, tmp_path: Path) -> None:
        f = tmp_path / "movie.srt"
        f.write_bytes(b"1\n00:00:01,000 --> 00:00:02,000\nHello\n")
        _, ft = detector.detect(str(f))
        assert ft == FileType.SIDECAR

    def test_xmp_sidecar(self, tmp_path: Path) -> None:
        f = tmp_path / "photo.xmp"
        f.write_bytes(b"<?xml version='1.0'?>")
        _, ft = detector.detect(str(f))
        assert ft == FileType.SIDECAR

    def test_txt_document(self, tmp_path: Path) -> None:
        f = tmp_path / "notes.txt"
        f.write_bytes(b"Hello world")
        mime, ft = detector.detect(str(f))
        assert ft == FileType.DOCUMENT
        assert mime == "text/plain"


# ======================================================================
# Unknown files
# ======================================================================

class TestUnknownFiles:
    def test_unknown_extension_and_magic(self) -> None:
        mime, ft = detector.detect(str(_FIXTURES / "sample.xyz"))
        assert mime == "application/octet-stream"
        assert ft == FileType.UNKNOWN

    def test_no_extension(self, tmp_path: Path) -> None:
        f = tmp_path / "noext"
        f.write_bytes(b"\xca\xfe\xba\xbe" + b"\x00" * 28)
        mime, ft = detector.detect(str(f))
        assert mime == "application/octet-stream"
        assert ft == FileType.UNKNOWN

    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        mime, ft = detector.detect(str(f))
        assert ft == FileType.UNKNOWN

    def test_nonexistent_file_with_extension(self) -> None:
        mime, ft = detector.detect("/nonexistent/photo.jpg")
        # Falls back to extension
        assert ft == FileType.IMAGE

    def test_nonexistent_file_no_extension(self) -> None:
        mime, ft = detector.detect("/nonexistent/unknown")
        assert ft == FileType.UNKNOWN


# ======================================================================
# Batch detection
# ======================================================================

class TestBatchDetection:
    def test_batch_preserves_order(self) -> None:
        paths = [
            str(_FIXTURES / "sample.jpg"),
            str(_FIXTURES / "sample.pdf"),
            str(_FIXTURES / "sample.mp4"),
            str(_FIXTURES / "sample.xyz"),
        ]
        results = detector.detect_batch(paths)
        assert len(results) == 4
        assert results[0][1] == FileType.IMAGE
        assert results[1][1] == FileType.DOCUMENT
        assert results[2][1] == FileType.VIDEO
        assert results[3][1] == FileType.UNKNOWN

    def test_batch_empty(self) -> None:
        assert detector.detect_batch([]) == []


# ======================================================================
# Magic vs extension priority
# ======================================================================

class TestMagicOverExtension:
    def test_magic_wins_over_wrong_extension(self, tmp_path: Path) -> None:
        """A JPEG file with .png extension should be detected as JPEG."""
        f = tmp_path / "misnamed.png"
        f.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00")
        mime, ft = detector.detect(str(f))
        assert mime == "image/jpeg"
        assert ft == FileType.IMAGE

    def test_pdf_with_txt_extension(self, tmp_path: Path) -> None:
        f = tmp_path / "sneaky.txt"
        f.write_bytes(b"%PDF-1.7\n")
        mime, ft = detector.detect(str(f))
        assert mime == "application/pdf"
        assert ft == FileType.DOCUMENT
