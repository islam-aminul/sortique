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
}

# Vorbis Comment (FLAC, OGG, OPUS) — lower-case keys.
_VORBIS_MAP = {
    "title": "title",
    "artist": "artist",
    "album": "album",
    "genre": "genre",
}

# MP4 / M4A / AAC — iTunes-style keys.
_MP4_MAP = {
    "title": "\xa9nam",
    "artist": "\xa9ART",
    "album": "\xa9alb",
    "genre": "\xa9gen",
}

# ASF / WMA — attribute names.
_ASF_MAP = {
    "title": "Title",
    "artist": "Author",
    "album": "WM/AlbumTitle",
    "genre": "WM/Genre",
}

# APE tags (APE, WavPack, Musepack) — mixed-case keys.
_APE_MAP = {
    "title": "Title",
    "artist": "Artist",
    "album": "Album",
    "genre": "Genre",
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
        """Extract audio tags.  Never raises."""
        try:
            audio = mutagen.File(filepath)
        except Exception:
            return AudioMetadata()

        if audio is None:
            return AudioMetadata()

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

        has_tags = any(v is not None for v in (title, artist, album))

        return AudioMetadata(
            title=title,
            artist=artist,
            album=album,
            genre=genre,
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
