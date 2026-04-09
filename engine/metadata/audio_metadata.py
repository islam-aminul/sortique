"""Audio metadata extraction using mutagen."""

from __future__ import annotations

from dataclasses import dataclass

import mutagen


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class AudioMetadata:
    """Metadata extracted from an audio file."""

    title: str | None = None
    artist: str | None = None
    album: str | None = None
    genre: str | None = None
    year: int | None = None
    duration_seconds: float | None = None
    has_tags: bool = False


# ---------------------------------------------------------------------------
# Tag key mappings per format
# ---------------------------------------------------------------------------

# ID3 (MP3, AIFF) — keys are four-character frame IDs.
_ID3_MAP = {
    "title": "TIT2",
    "artist": "TPE1",
    "album": "TALB",
    "genre": "TCON",
    "year": "TDRC",  # Recording date/year
}

# Vorbis Comment (FLAC, OGG, OPUS) — lower-case keys.
_VORBIS_MAP = {
    "title": "title",
    "artist": "artist",
    "album": "album",
    "genre": "genre",
    "year": "date",  # Year/date field
}

# MP4 / M4A / AAC — iTunes-style keys.
_MP4_MAP = {
    "title": "\xa9nam",
    "artist": "\xa9ART",
    "album": "\xa9alb",
    "genre": "\xa9gen",
    "year": "\xa9day",  # Year field
}

# ASF / WMA — attribute names.
_ASF_MAP = {
    "title": "Title",
    "artist": "Author",
    "album": "WM/AlbumTitle",
    "genre": "WM/Genre",
    "year": "WM/Year",
}

# APE tags (APE, WavPack, Musepack) — mixed-case keys.
_APE_MAP = {
    "title": "Title",
    "artist": "Artist",
    "album": "Album",
    "genre": "Genre",
    "year": "Year",
}


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------

class AudioMetadataExtractor:
    """Audio metadata extraction using *mutagen*.

    Supports MP3 (ID3), FLAC, OGG, M4A/AAC, OPUS, WMA, AIFF, APE and
    any other format that :func:`mutagen.File` can auto-detect.
    """

    def extract(self, filepath: str) -> AudioMetadata:
        """Extract audio tags.  Never raises.
        
        Falls back to filename (without extension) as title when no title tag is found.
        """
        try:
            audio = mutagen.File(filepath)
        except Exception:
            # If mutagen fails, still provide filename as title
            import os
            filename = os.path.basename(filepath)
            title = os.path.splitext(filename)[0]
            return AudioMetadata(title=title, has_tags=False)

        if audio is None:
            # If mutagen can't identify the file, still provide filename as title
            import os
            filename = os.path.basename(filepath)
            title = os.path.splitext(filename)[0]
            return AudioMetadata(title=title, has_tags=False)

        # Duration (available on almost every mutagen type).
        duration: float | None = None
        info = getattr(audio, "info", None)
        if info is not None:
            raw = getattr(info, "length", None)
            if raw is not None:
                try:
                    duration = round(float(raw), 3)
                except (ValueError, TypeError):
                    pass

        # Pick the right key mapping for this format.
        key_map = self._select_key_map(audio)

        title = self._read_tag(audio, key_map, "title")
        artist = self._read_tag(audio, key_map, "artist")
        album = self._read_tag(audio, key_map, "album")
        genre = self._read_tag(audio, key_map, "genre")
        year = self._read_year_tag(audio, key_map)

        # has_tags is True only if metadata tags are present (not filename fallback)
        has_tags = any(v is not None for v in (title, artist, album))

        # Fallback to filename (without extension) if title is unavailable
        if title is None:
            import os
            filename = os.path.basename(filepath)
            title = os.path.splitext(filename)[0]

        return AudioMetadata(
            title=title,
            artist=artist,
            album=album,
            genre=genre,
            year=year,
            duration_seconds=duration,
            has_tags=has_tags,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _select_key_map(audio: mutagen.FileType) -> dict[str, str]:
        """Choose the tag-key mapping appropriate for *audio*'s type."""
        cls_name = type(audio).__name__

        if cls_name in ("MP3", "AIFF"):
            return _ID3_MAP
        if cls_name in ("FLAC", "OggVorbis", "OggOpus"):
            return _VORBIS_MAP
        if cls_name in ("MP4", "M4A", "AAC"):
            return _MP4_MAP
        if cls_name in ("ASF",):
            return _ASF_MAP
        if cls_name in ("APEv2File", "APEv2", "MonkeysAudio", "WavPack", "Musepack"):
            return _APE_MAP

        # Fallback: try Vorbis-style (most permissive).
        return _VORBIS_MAP

    @staticmethod
    def _read_tag(audio: mutagen.FileType, key_map: dict[str, str], field: str) -> str | None:
        """Read a single tag value, returning ``None`` when absent."""
        key = key_map.get(field)
        if key is None:
            return None

        try:
            val = audio[key]
        except (KeyError, TypeError):
            # Also try audio.tags dict directly for some formats.
            tags = getattr(audio, "tags", None)
            if tags is None:
                return None
            try:
                val = tags[key]
            except (KeyError, TypeError):
                return None

        # mutagen returns lists for most formats.
        if isinstance(val, list):
            val = val[0] if val else None

        if val is None:
            return None

        text = str(val).strip()
        return text if text else None

    @staticmethod
    def _read_year_tag(audio: mutagen.FileType, key_map: dict[str, str]) -> int | None:
        """Read year tag and convert to integer.
        
        Handles various year formats:
        - Simple year: "2024"
        - ISO date: "2024-03-15"
        - ID3 TDRC timestamp objects
        """
        key = key_map.get("year")
        if key is None:
            return None

        try:
            val = audio[key]
        except (KeyError, TypeError):
            # Try audio.tags dict directly
            tags = getattr(audio, "tags", None)
            if tags is None:
                return None
            try:
                val = tags[key]
            except (KeyError, TypeError):
                return None

        # mutagen returns lists for most formats
        if isinstance(val, list):
            val = val[0] if val else None

        if val is None:
            return None

        # Handle ID3 timestamp objects (TDRC)
        if hasattr(val, "year"):
            return int(val.year)

        # Handle string values
        text = str(val).strip()
        if not text:
            return None

        # Extract year from ISO date format (YYYY-MM-DD)
        if "-" in text:
            text = text.split("-")[0]

        # Try to parse as integer
        try:
            year = int(text)
            # Sanity check: year should be reasonable
            if 1900 <= year <= 2100:
                return year
        except (ValueError, TypeError):
            pass

        return None
