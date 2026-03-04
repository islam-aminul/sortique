"""Tests for sortique.engine.metadata.audio_metadata."""

from __future__ import annotations

import io
import struct
from pathlib import Path

import pytest
from mutagen.id3 import ID3, TALB, TCON, TIT2, TPE1
from mutagen.mp3 import MP3

from sortique.engine.metadata.audio_metadata import AudioMetadata, AudioMetadataExtractor


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def extractor():
    return AudioMetadataExtractor()


# ---------------------------------------------------------------------------
# MP3 fixture builder
# ---------------------------------------------------------------------------

def _build_mp3_frames(duration_ms: int = 1000) -> bytes:
    """Build the absolute minimum MPEG audio bytes that mutagen accepts.

    Produces a single MPEG-1 Layer-3 frame (mono, 128 kbps, 44100 Hz).
    One such frame is ~26 ms; we repeat to reach approximately *duration_ms*.
    """
    # MPEG1 Layer3 128kbps 44100Hz mono → frame size = 417 bytes
    # Frame header: 0xFF 0xFB 0x90 0x00
    header = b"\xff\xfb\x90\x00"
    frame_size = 417
    padding = b"\x00" * (frame_size - len(header))
    single_frame = header + padding

    frames_needed = max(1, duration_ms // 26)
    return single_frame * frames_needed


def _write_tagged_mp3(
    path: Path,
    *,
    title: str | None = None,
    artist: str | None = None,
    album: str | None = None,
    genre: str | None = None,
) -> str:
    """Write a minimal MP3 with ID3v2 tags and return its path."""
    path.write_bytes(_build_mp3_frames())

    # mutagen can create ID3 tags on an existing MPEG file.
    audio = MP3(str(path))
    audio.add_tags()

    if title is not None:
        audio.tags.add(TIT2(encoding=3, text=[title]))
    if artist is not None:
        audio.tags.add(TPE1(encoding=3, text=[artist]))
    if album is not None:
        audio.tags.add(TALB(encoding=3, text=[album]))
    if genre is not None:
        audio.tags.add(TCON(encoding=3, text=[genre]))

    audio.save()
    return str(path)


def _write_untagged_mp3(path: Path) -> str:
    """Write a minimal MP3 with **no** ID3 tags."""
    path.write_bytes(_build_mp3_frames())
    return str(path)


# ===========================================================================
# 1. AudioMetadata dataclass
# ===========================================================================

class TestAudioMetadataDataclass:

    def test_defaults(self):
        m = AudioMetadata()
        assert m.title is None
        assert m.artist is None
        assert m.album is None
        assert m.genre is None
        assert m.duration_seconds is None
        assert m.has_tags is False

    def test_has_tags_true(self):
        m = AudioMetadata(title="Song", has_tags=True)
        assert m.has_tags is True


# ===========================================================================
# 2. Tagged MP3
# ===========================================================================

class TestTaggedMp3:

    def test_all_tags(self, extractor, tmp_path):
        f = _write_tagged_mp3(
            tmp_path / "song.mp3",
            title="My Song",
            artist="The Band",
            album="Greatest Hits",
            genre="Rock",
        )
        result = extractor.extract(f)
        assert result.title == "My Song"
        assert result.artist == "The Band"
        assert result.album == "Greatest Hits"
        assert result.genre == "Rock"
        assert result.has_tags is True

    def test_title_only(self, extractor, tmp_path):
        f = _write_tagged_mp3(tmp_path / "t.mp3", title="Title Only")
        result = extractor.extract(f)
        assert result.title == "Title Only"
        assert result.artist is None
        assert result.has_tags is True

    def test_artist_only(self, extractor, tmp_path):
        f = _write_tagged_mp3(tmp_path / "a.mp3", artist="Solo Artist")
        result = extractor.extract(f)
        assert result.artist == "Solo Artist"
        assert result.has_tags is True

    def test_album_only(self, extractor, tmp_path):
        f = _write_tagged_mp3(tmp_path / "al.mp3", album="The Album")
        result = extractor.extract(f)
        assert result.album == "The Album"
        assert result.has_tags is True

    def test_genre_only_no_has_tags(self, extractor, tmp_path):
        """genre alone does NOT set has_tags (requires title/artist/album)."""
        f = _write_tagged_mp3(tmp_path / "g.mp3", genre="Jazz")
        result = extractor.extract(f)
        assert result.genre == "Jazz"
        assert result.has_tags is False

    def test_duration_is_positive(self, extractor, tmp_path):
        f = _write_tagged_mp3(tmp_path / "dur.mp3", title="D")
        result = extractor.extract(f)
        assert result.duration_seconds is not None
        assert result.duration_seconds > 0


# ===========================================================================
# 3. Untagged file
# ===========================================================================

class TestUntaggedFile:

    def test_untagged_mp3(self, extractor, tmp_path):
        f = _write_untagged_mp3(tmp_path / "bare.mp3")
        result = extractor.extract(f)
        assert result.title is None
        assert result.artist is None
        assert result.album is None
        assert result.has_tags is False
        # Duration should still be available from MPEG frames.
        assert result.duration_seconds is not None

    def test_non_audio_file(self, extractor, tmp_path):
        f = tmp_path / "readme.txt"
        f.write_text("hello")
        result = extractor.extract(str(f))
        assert result.has_tags is False
        assert result.duration_seconds is None

    def test_empty_file(self, extractor, tmp_path):
        f = tmp_path / "empty.mp3"
        f.write_bytes(b"")
        result = extractor.extract(str(f))
        assert result.has_tags is False

    def test_missing_file(self, extractor):
        result = extractor.extract("/nonexistent/audio.mp3")
        assert result.has_tags is False
        assert result.duration_seconds is None


# ===========================================================================
# 4. has_tags logic
# ===========================================================================

class TestHasTagsLogic:

    def test_true_with_title(self, extractor, tmp_path):
        f = _write_tagged_mp3(tmp_path / "t.mp3", title="T")
        assert extractor.extract(f).has_tags is True

    def test_true_with_artist(self, extractor, tmp_path):
        f = _write_tagged_mp3(tmp_path / "a.mp3", artist="A")
        assert extractor.extract(f).has_tags is True

    def test_true_with_album(self, extractor, tmp_path):
        f = _write_tagged_mp3(tmp_path / "al.mp3", album="A")
        assert extractor.extract(f).has_tags is True

    def test_false_with_genre_only(self, extractor, tmp_path):
        f = _write_tagged_mp3(tmp_path / "g.mp3", genre="Pop")
        assert extractor.extract(f).has_tags is False

    def test_false_with_no_tags(self, extractor, tmp_path):
        f = _write_untagged_mp3(tmp_path / "n.mp3")
        assert extractor.extract(f).has_tags is False


# ===========================================================================
# 5. Graceful failure
# ===========================================================================

class TestGracefulFailure:

    def test_binary_garbage(self, extractor, tmp_path):
        f = tmp_path / "garbage.mp3"
        f.write_bytes(b"\xff" * 500)
        result = extractor.extract(str(f))
        assert isinstance(result, AudioMetadata)

    def test_never_raises(self, extractor, tmp_path):
        for payload in (b"", b"\x00" * 100, b"PK\x03\x04" + b"\x00" * 50):
            f = tmp_path / "test_file"
            f.write_bytes(payload)
            result = extractor.extract(str(f))
            assert isinstance(result, AudioMetadata)
