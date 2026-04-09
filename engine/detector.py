"""Content detection using magic bytes with extension fallback."""

from __future__ import annotations

import os
from typing import Final

from sortique.constants import (
    EXTENSION_MAP,
    FileType,
)

# Minimum bytes to read for reliable detection (covers ftyp box offsets).
_HEADER_SIZE: Final[int] = 32

# Extension → MIME for the fallback path (common cases).
_EXT_MIME: dict[str, str] = {
    # image
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".gif": "image/gif", ".bmp": "image/bmp",
    ".tiff": "image/tiff", ".tif": "image/tiff",
    ".webp": "image/webp", ".heic": "image/heic", ".heif": "image/heif",
    ".avif": "image/avif", ".jxl": "image/jxl",
    ".svg": "image/svg+xml", ".ico": "image/x-icon",
    ".psd": "image/vnd.adobe.photoshop",
    # raw
    ".cr2": "image/x-canon-cr2", ".cr3": "image/x-canon-cr3",
    ".nef": "image/x-nikon-nef", ".arw": "image/x-sony-arw",
    ".dng": "image/x-adobe-dng", ".orf": "image/x-olympus-orf",
    ".raf": "image/x-fuji-raf", ".rw2": "image/x-panasonic-rw2",
    ".pef": "image/x-pentax-pef", ".srw": "image/x-samsung-srw",
    # video
    ".mp4": "video/mp4", ".mov": "video/quicktime",
    ".avi": "video/x-msvideo", ".mkv": "video/x-matroska",
    ".webm": "video/webm", ".wmv": "video/x-ms-wmv",
    ".flv": "video/x-flv", ".m4v": "video/x-m4v",
    ".mpg": "video/mpeg", ".mpeg": "video/mpeg",
    ".3gp": "video/3gpp", ".mts": "video/mp2t",
    # audio
    ".mp3": "audio/mpeg", ".wav": "audio/wav",
    ".flac": "audio/flac", ".aac": "audio/aac",
    ".ogg": "audio/ogg", ".wma": "audio/x-ms-wma",
    ".m4a": "audio/mp4", ".opus": "audio/opus",
    ".aiff": "audio/aiff", ".aif": "audio/aiff",
    # document
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain", ".csv": "text/csv", ".md": "text/markdown",
    ".html": "text/html", ".htm": "text/html",
    ".xml": "application/xml", ".json": "application/json",
    # sidecar
    ".xmp": "application/rdf+xml", ".srt": "application/x-subrip",
    ".aae": "application/xml",
}

# ftyp brand → (mime, FileType)
_FTYP_BRANDS: dict[bytes, tuple[str, FileType]] = {
    b"isom": ("video/mp4", FileType.VIDEO),
    b"iso2": ("video/mp4", FileType.VIDEO),
    b"mp41": ("video/mp4", FileType.VIDEO),
    b"mp42": ("video/mp4", FileType.VIDEO),
    b"M4V ": ("video/x-m4v", FileType.VIDEO),
    b"M4A ": ("audio/mp4", FileType.AUDIO),
    b"M4B ": ("audio/mp4", FileType.AUDIO),
    b"mp4a": ("audio/mp4", FileType.AUDIO),
    b"qt  ": ("video/quicktime", FileType.VIDEO),
    b"3gp4": ("video/3gpp", FileType.VIDEO),
    b"3gp5": ("video/3gpp", FileType.VIDEO),
    b"3gp6": ("video/3gpp", FileType.VIDEO),
    b"heic": ("image/heic", FileType.IMAGE),
    b"heix": ("image/heic", FileType.IMAGE),
    b"hevc": ("image/heic", FileType.IMAGE),
    b"mif1": ("image/heif", FileType.IMAGE),
    b"msf1": ("image/heif-sequence", FileType.IMAGE),
    b"avif": ("image/avif", FileType.IMAGE),
    b"avis": ("image/avif-sequence", FileType.IMAGE),
}


class ContentDetector:
    """Detects file content type using magic bytes first, extension fallback second."""

    # Fixed-result magic signatures: (prefix_bytes, mime, FileType).
    # Sorted longest-first at class load time so longer prefixes match first.
    _MAGIC_TABLE: list[tuple[bytes, str, FileType]] = sorted(
        [
            (b"\xff\xd8\xff",             "image/jpeg",       FileType.IMAGE),
            (b"\x89PNG\r\n\x1a\n",        "image/png",        FileType.IMAGE),
            (b"GIF87a",                    "image/gif",        FileType.IMAGE),
            (b"GIF89a",                    "image/gif",        FileType.IMAGE),
            (b"BM",                        "image/bmp",        FileType.IMAGE),
            (b"II\x2a\x00",               "image/tiff",       FileType.IMAGE),
            (b"MM\x00\x2a",               "image/tiff",       FileType.IMAGE),
            (b"\x1a\x45\xdf\xa3",         "video/x-matroska", FileType.VIDEO),
            (b"ID3",                       "audio/mpeg",       FileType.AUDIO),
            (b"\xff\xfb",                  "audio/mpeg",       FileType.AUDIO),
            (b"\xff\xf3",                  "audio/mpeg",       FileType.AUDIO),
            (b"\xff\xf2",                  "audio/mpeg",       FileType.AUDIO),
            (b"fLaC",                      "audio/flac",       FileType.AUDIO),
            (b"OggS",                      "audio/ogg",        FileType.AUDIO),
            (b"%PDF",                      "application/pdf",  FileType.DOCUMENT),
            (b"PK\x03\x04",               "application/zip",  FileType.DOCUMENT),
        ],
        key=lambda t: len(t[0]),
        reverse=True,
    )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect(self, filepath: str) -> tuple[str, FileType]:
        """Return ``(mime_type, FileType)`` for *filepath*.

        Strategy:
        1. Read first 32 bytes.
        2. Try fixed magic signatures (longest prefix first).
        3. For ambiguous headers (``RIFF``, ftyp-box), read more bytes.
        4. Fall back to extension mapping from ``constants.EXTENSION_MAP``.
        5. Last resort: ``('application/octet-stream', FileType.UNKNOWN)``.
        """
        try:
            with open(filepath, "rb") as f:
                header = f.read(_HEADER_SIZE)
        except OSError:
            return self._fallback_extension(filepath)

        if not header:
            return self._fallback_extension(filepath)

        # Ambiguous multi-format containers — check before fixed table.
        if header[:4] == b"RIFF":
            result = self._check_riff(filepath)
            if result is not None:
                return result

        if self._looks_like_ftyp(header):
            result = self._check_ftyp(filepath)
            if result is not None:
                return result

        result = self._check_magic(header)
        if result is not None:
            return result

        return self._fallback_extension(filepath)

    def detect_batch(self, filepaths: list[str]) -> list[tuple[str, FileType]]:
        """Detect content type for every path in *filepaths* (order preserved)."""
        return [self.detect(fp) for fp in filepaths]

    # ------------------------------------------------------------------
    # Magic-byte matching
    # ------------------------------------------------------------------

    def _check_magic(self, header: bytes) -> tuple[str, FileType] | None:
        """Match *header* against the fixed magic-signature table."""
        for prefix, mime, ftype in self._MAGIC_TABLE:
            if header[: len(prefix)] == prefix:
                return mime, ftype
        return None

    # ------------------------------------------------------------------
    # ISO base media file format (ftyp box)
    # ------------------------------------------------------------------

    @staticmethod
    def _looks_like_ftyp(header: bytes) -> bool:
        """Heuristic: bytes 4-8 are ``ftyp`` in an ISO BMFF container."""
        return len(header) >= 8 and header[4:8] == b"ftyp"

    def _check_ftyp(self, filepath: str) -> tuple[str, FileType] | None:
        """Read the ftyp major-brand from an ISO base media file.

        The ISO BMFF (ftyp) container is shared by video (``.mp4``),
        audio (``.m4a``, ``.aac``), and image (HEIC/AVIF) files.
        Ambiguous brands like ``isom``, ``mp42``, and ``3gp*`` can wrap
        either audio-only or audio+video tracks.  When the brand itself
        is ambiguous (maps to VIDEO), the file extension is checked:
        if it is a known audio extension the result is overridden to
        AUDIO so that ``.mp3``, ``.m4a``, etc. are never misclassified
        as video.
        """
        try:
            with open(filepath, "rb") as f:
                buf = f.read(32)
        except OSError:
            return None

        if len(buf) < 12 or buf[4:8] != b"ftyp":
            return None

        brand = buf[8:12]
        result = _FTYP_BRANDS.get(brand)

        if result is None:
            # Fallback: any ftyp box is likely video/mp4.
            result = ("video/mp4", FileType.VIDEO)

        # --- Extension-based disambiguation for audio containers -------
        # Many audio files (.m4a, .mp3, .aac, .ogg) are wrapped in the
        # same ISO BMFF container used by video.  The ftyp brand alone
        # cannot distinguish them.  Trust the extension when it clearly
        # indicates audio.
        if result[1] == FileType.VIDEO:
            _, ext = os.path.splitext(filepath)
            ext_lower = ext.lower()
            ext_ftype = EXTENSION_MAP.get(ext_lower)
            if ext_ftype == FileType.AUDIO:
                mime = _EXT_MIME.get(ext_lower, "audio/mp4")
                return mime, FileType.AUDIO

        return result

    # ------------------------------------------------------------------
    # RIFF container
    # ------------------------------------------------------------------

    def _check_riff(self, filepath: str) -> tuple[str, FileType] | None:
        """Disambiguate RIFF containers (WebP vs AVI vs WAV)."""
        try:
            with open(filepath, "rb") as f:
                buf = f.read(16)
        except OSError:
            return None

        if len(buf) < 12 or buf[:4] != b"RIFF":
            return None

        fourcc = buf[8:12]
        if fourcc == b"WEBP":
            return "image/webp", FileType.IMAGE
        if fourcc == b"AVI ":
            return "video/x-msvideo", FileType.VIDEO
        if fourcc == b"WAVE":
            return "audio/wav", FileType.AUDIO

        return None

    # ------------------------------------------------------------------
    # Extension fallback
    # ------------------------------------------------------------------

    @staticmethod
    def _fallback_extension(filepath: str) -> tuple[str, FileType]:
        """Map file extension to ``(mime, FileType)`` via constants."""
        _, ext = os.path.splitext(filepath)
        ext_lower = ext.lower()

        ftype = EXTENSION_MAP.get(ext_lower)
        if ftype is not None:
            mime = _EXT_MIME.get(ext_lower, "application/octet-stream")
            return mime, ftype

        return "application/octet-stream", FileType.UNKNOWN
