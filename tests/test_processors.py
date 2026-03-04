"""Tests for sortique.engine.processors (video, audio, document)."""

from __future__ import annotations

import os

import pytest

from sortique.data.config_manager import ConfigManager
from sortique.engine.processors import ProcessResult
from sortique.engine.processors.audio_processor import AudioProcessor
from sortique.engine.processors.document_processor import DocumentProcessor
from sortique.engine.processors.video_processor import VideoProcessor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_file(tmp_path, name: str, content: bytes = b"dummy-data") -> str:
    """Create a file with given name under *tmp_path*."""
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return str(path)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def config(tmp_path):
    return ConfigManager(config_dir=str(tmp_path / "cfg"))


@pytest.fixture()
def video_proc(config):
    return VideoProcessor(config)


@pytest.fixture()
def audio_proc(config):
    return AudioProcessor(config)


@pytest.fixture()
def doc_proc():
    return DocumentProcessor()


# ===========================================================================
# 1.  ProcessResult dataclass
# ===========================================================================

class TestProcessResult:

    def test_success_fields(self):
        r = ProcessResult(
            success=True,
            source_path="/src/a.mp4",
            dest_path="/dst/a.mp4",
            bytes_copied=1024,
            is_sidecar=False,
            error=None,
        )
        assert r.success is True
        assert r.bytes_copied == 1024
        assert r.is_sidecar is False
        assert r.error is None

    def test_failure_fields(self):
        r = ProcessResult(
            success=False,
            source_path="/src/a.mp4",
            dest_path="/dst/a.mp4",
            bytes_copied=0,
            is_sidecar=False,
            error="file not found",
        )
        assert r.success is False
        assert r.error == "file not found"

    def test_sidecar_flag(self):
        r = ProcessResult(
            success=True,
            source_path="/src/a.srt",
            dest_path="/dst/a.srt",
            bytes_copied=100,
            is_sidecar=True,
            error=None,
        )
        assert r.is_sidecar is True


# ===========================================================================
# 2.  VideoProcessor — process (copy)
# ===========================================================================

class TestVideoProcessorCopy:

    def test_copies_video(self, video_proc, tmp_path):
        src = _make_file(tmp_path, "clip.mp4", b"fake-mp4-data-1234")
        dst = str(tmp_path / "out" / "clip.mp4")
        result = video_proc.process(src, dst)

        assert result.success is True
        assert os.path.exists(dst)
        assert result.bytes_copied == len(b"fake-mp4-data-1234")
        assert result.is_sidecar is False
        assert result.error is None

    def test_preserves_content(self, video_proc, tmp_path):
        content = b"video-bytes-" * 100
        src = _make_file(tmp_path, "v.mov", content)
        dst = str(tmp_path / "copy.mov")
        video_proc.process(src, dst)
        assert open(dst, "rb").read() == content

    def test_creates_parent_dirs(self, video_proc, tmp_path):
        src = _make_file(tmp_path, "v.mp4")
        dst = str(tmp_path / "a" / "b" / "c" / "v.mp4")
        result = video_proc.process(src, dst)
        assert result.success is True
        assert os.path.exists(dst)

    def test_missing_source_returns_error(self, video_proc, tmp_path):
        result = video_proc.process(
            str(tmp_path / "missing.mp4"),
            str(tmp_path / "out.mp4"),
        )
        assert result.success is False
        assert result.error is not None
        assert result.bytes_copied == 0


# ===========================================================================
# 3.  VideoProcessor — find_sidecars
# ===========================================================================

class TestVideoFindSidecars:
    """Sidecar discovery using stem matching."""

    def test_standard_stem_match(self, video_proc, tmp_path):
        """VIDEO_0001.srt is found for VIDEO_0001.mp4."""
        _make_file(tmp_path, "VIDEO_0001.mp4")
        _make_file(tmp_path, "VIDEO_0001.srt")
        _make_file(tmp_path, "VIDEO_0001.thm")

        video_path = str(tmp_path / "VIDEO_0001.mp4")
        sidecars = video_proc.find_sidecars(video_path)

        names = [os.path.basename(s) for s in sidecars]
        assert "VIDEO_0001.srt" in names
        assert "VIDEO_0001.thm" in names

    def test_extended_stem_match(self, video_proc, tmp_path):
        """VIDEO_0001.mp4.srt (extended stem) is found for VIDEO_0001.mp4."""
        _make_file(tmp_path, "VIDEO_0001.mp4")
        _make_file(tmp_path, "VIDEO_0001.mp4.srt")
        _make_file(tmp_path, "VIDEO_0001.mp4.xmp")

        video_path = str(tmp_path / "VIDEO_0001.mp4")
        sidecars = video_proc.find_sidecars(video_path)

        names = [os.path.basename(s) for s in sidecars]
        assert "VIDEO_0001.mp4.srt" in names
        assert "VIDEO_0001.mp4.xmp" in names

    def test_both_match_types(self, video_proc, tmp_path):
        """Both standard and extended matches are returned together."""
        _make_file(tmp_path, "clip.mov")
        _make_file(tmp_path, "clip.srt")       # standard
        _make_file(tmp_path, "clip.mov.xmp")   # extended

        video_path = str(tmp_path / "clip.mov")
        sidecars = video_proc.find_sidecars(video_path)

        names = [os.path.basename(s) for s in sidecars]
        assert "clip.srt" in names
        assert "clip.mov.xmp" in names

    def test_video_file_not_in_results(self, video_proc, tmp_path):
        """The video file itself must not appear in the sidecar list."""
        _make_file(tmp_path, "VIDEO.mp4")
        _make_file(tmp_path, "VIDEO.srt")

        video_path = str(tmp_path / "VIDEO.mp4")
        sidecars = video_proc.find_sidecars(video_path)

        names = [os.path.basename(s) for s in sidecars]
        assert "VIDEO.mp4" not in names

    def test_non_sidecar_extension_ignored(self, video_proc, tmp_path):
        """Files with non-sidecar extensions are ignored."""
        _make_file(tmp_path, "clip.mp4")
        _make_file(tmp_path, "clip.jpg")   # not a sidecar extension
        _make_file(tmp_path, "clip.txt")   # not a sidecar extension

        video_path = str(tmp_path / "clip.mp4")
        sidecars = video_proc.find_sidecars(video_path)
        assert len(sidecars) == 0

    def test_different_stem_not_matched(self, video_proc, tmp_path):
        """Files with a completely different stem are not matched."""
        _make_file(tmp_path, "clipA.mp4")
        _make_file(tmp_path, "clipB.srt")

        video_path = str(tmp_path / "clipA.mp4")
        sidecars = video_proc.find_sidecars(video_path)
        assert len(sidecars) == 0

    def test_no_sidecars_found(self, video_proc, tmp_path):
        _make_file(tmp_path, "lonely.mp4")
        video_path = str(tmp_path / "lonely.mp4")
        sidecars = video_proc.find_sidecars(video_path)
        assert sidecars == []

    def test_case_insensitive_extension(self, video_proc, tmp_path):
        """Sidecar extension matching is case-insensitive."""
        _make_file(tmp_path, "clip.mp4")
        _make_file(tmp_path, "clip.SRT")

        video_path = str(tmp_path / "clip.mp4")
        sidecars = video_proc.find_sidecars(video_path)
        names = [os.path.basename(s) for s in sidecars]
        assert "clip.SRT" in names

    def test_empty_sidecar_extensions_config(self, tmp_path):
        """When sidecar_extensions is empty, nothing is found."""
        config = ConfigManager(config_dir=str(tmp_path / "cfg"))
        config.set("sidecar_extensions", [])
        proc = VideoProcessor(config)

        _make_file(tmp_path, "clip.mp4")
        _make_file(tmp_path, "clip.srt")

        # Override config to return empty list
        # (the default has sidecar extensions, so we need to force empty)
        original_get = config.get
        config.get = lambda key, default=None: (
            [] if key == "sidecar_extensions" else original_get(key, default)
        )

        sidecars = proc.find_sidecars(str(tmp_path / "clip.mp4"))
        assert sidecars == []

    def test_longer_stem_not_false_matched(self, video_proc, tmp_path):
        """VIDEO_0001_extra.srt should NOT match VIDEO_0001.mp4 as standard stem."""
        _make_file(tmp_path, "VIDEO_0001.mp4")
        _make_file(tmp_path, "VIDEO_0001_extra.srt")

        video_path = str(tmp_path / "VIDEO_0001.mp4")
        sidecars = video_proc.find_sidecars(video_path)
        names = [os.path.basename(s) for s in sidecars]
        assert "VIDEO_0001_extra.srt" not in names

    def test_results_are_sorted(self, video_proc, tmp_path):
        _make_file(tmp_path, "clip.mp4")
        _make_file(tmp_path, "clip.xmp")
        _make_file(tmp_path, "clip.srt")
        _make_file(tmp_path, "clip.thm")

        video_path = str(tmp_path / "clip.mp4")
        sidecars = video_proc.find_sidecars(video_path)
        assert sidecars == sorted(sidecars)


# ===========================================================================
# 4.  VideoProcessor — copy_with_sidecars
# ===========================================================================

class TestVideoCopyWithSidecars:

    def test_copies_video_and_sidecars(self, video_proc, tmp_path):
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        _make_file(src_dir, "VID_001.mp4", b"video-data")
        _make_file(src_dir, "VID_001.srt", b"subtitle-data")
        _make_file(src_dir, "VID_001.thm", b"thumb-data")

        dest_dir = str(tmp_path / "dst")
        results = video_proc.copy_with_sidecars(
            str(src_dir / "VID_001.mp4"),
            dest_dir,
            "2024-01-01 -- VID_001",
        )

        assert len(results) == 3  # video + 2 sidecars

        # Video result
        assert results[0].success is True
        assert results[0].is_sidecar is False
        assert results[0].dest_path.endswith(".mp4")

        # Sidecar results
        sidecar_results = results[1:]
        for sr in sidecar_results:
            assert sr.success is True
            assert sr.is_sidecar is True

        # All files exist at destination
        for r in results:
            assert os.path.exists(r.dest_path)

    def test_sidecar_uses_new_stem(self, video_proc, tmp_path):
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        _make_file(src_dir, "old.mp4", b"v")
        _make_file(src_dir, "old.srt", b"s")

        dest_dir = str(tmp_path / "dst")
        results = video_proc.copy_with_sidecars(
            str(src_dir / "old.mp4"), dest_dir, "new_name",
        )

        video_name = os.path.basename(results[0].dest_path)
        assert video_name == "new_name.mp4"

        sidecar_name = os.path.basename(results[1].dest_path)
        assert sidecar_name == "new_name.srt"

    def test_extended_sidecar_stem(self, video_proc, tmp_path):
        """Extended stem sidecars (video.mp4.xmp) keep the full pattern."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        _make_file(src_dir, "clip.mp4", b"v")
        _make_file(src_dir, "clip.mp4.xmp", b"x")

        dest_dir = str(tmp_path / "dst")
        results = video_proc.copy_with_sidecars(
            str(src_dir / "clip.mp4"), dest_dir, "renamed",
        )

        sidecar_name = os.path.basename(results[1].dest_path)
        assert sidecar_name == "renamed.mp4.xmp"

    def test_no_sidecars(self, video_proc, tmp_path):
        src = _make_file(tmp_path, "solo.mp4", b"video")
        dest_dir = str(tmp_path / "dst")
        results = video_proc.copy_with_sidecars(src, dest_dir, "solo")
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].is_sidecar is False


# ===========================================================================
# 5.  AudioProcessor — process (copy)
# ===========================================================================

class TestAudioProcessorCopy:

    def test_copies_audio(self, audio_proc, tmp_path):
        content = b"fake-mp3-audio-data"
        src = _make_file(tmp_path, "song.mp3", content)
        dst = str(tmp_path / "out" / "song.mp3")
        result = audio_proc.process(src, dst)

        assert result.success is True
        assert os.path.exists(dst)
        assert result.bytes_copied == len(content)
        assert result.is_sidecar is False
        assert result.error is None

    def test_preserves_content(self, audio_proc, tmp_path):
        content = b"audio-bytes-" * 50
        src = _make_file(tmp_path, "track.flac", content)
        dst = str(tmp_path / "track.flac")
        audio_proc.process(src, dst)
        assert open(dst, "rb").read() == content

    def test_creates_parent_dirs(self, audio_proc, tmp_path):
        src = _make_file(tmp_path, "song.m4a")
        dst = str(tmp_path / "a" / "b" / "song.m4a")
        result = audio_proc.process(src, dst)
        assert result.success is True
        assert os.path.exists(dst)

    def test_missing_source_returns_error(self, audio_proc, tmp_path):
        result = audio_proc.process(
            str(tmp_path / "missing.mp3"),
            str(tmp_path / "out.mp3"),
        )
        assert result.success is False
        assert result.error is not None
        assert result.bytes_copied == 0

    def test_various_formats(self, audio_proc, tmp_path):
        """Various audio formats are all copy-only."""
        for ext in (".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a"):
            src = _make_file(tmp_path, f"track{ext}", b"audio")
            dst = str(tmp_path / "out" / f"track{ext}")
            result = audio_proc.process(src, dst)
            assert result.success is True


# ===========================================================================
# 6.  DocumentProcessor — process (copy)
# ===========================================================================

class TestDocumentProcessorCopy:

    def test_copies_document(self, doc_proc, tmp_path):
        content = b"PDF content here" * 10
        src = _make_file(tmp_path, "report.pdf", content)
        dst = str(tmp_path / "out" / "report.pdf")
        result = doc_proc.process(src, dst)

        assert result.success is True
        assert os.path.exists(dst)
        assert result.bytes_copied == len(content)
        assert result.is_sidecar is False
        assert result.error is None

    def test_preserves_content(self, doc_proc, tmp_path):
        content = b"spreadsheet data " * 20
        src = _make_file(tmp_path, "data.xlsx", content)
        dst = str(tmp_path / "data.xlsx")
        doc_proc.process(src, dst)
        assert open(dst, "rb").read() == content

    def test_creates_parent_dirs(self, doc_proc, tmp_path):
        src = _make_file(tmp_path, "doc.pdf")
        dst = str(tmp_path / "x" / "y" / "doc.pdf")
        result = doc_proc.process(src, dst)
        assert result.success is True
        assert os.path.exists(dst)

    def test_missing_source_returns_error(self, doc_proc, tmp_path):
        result = doc_proc.process(
            str(tmp_path / "missing.pdf"),
            str(tmp_path / "out.pdf"),
        )
        assert result.success is False
        assert result.error is not None
        assert result.bytes_copied == 0

    def test_size_extraction(self, doc_proc, tmp_path):
        """bytes_copied correctly reflects the file size."""
        content = b"x" * 4096
        src = _make_file(tmp_path, "doc.txt", content)
        dst = str(tmp_path / "doc.txt")
        result = doc_proc.process(src, dst)
        assert result.bytes_copied == 4096

    def test_various_formats(self, doc_proc, tmp_path):
        """Various document formats are all copy-only."""
        for ext in (".pdf", ".docx", ".xlsx", ".pptx", ".txt", ".csv"):
            src = _make_file(tmp_path, f"file{ext}", b"doc-content")
            dst = str(tmp_path / "out" / f"file{ext}")
            result = doc_proc.process(src, dst)
            assert result.success is True


# ===========================================================================
# 7.  Progress callback
# ===========================================================================

class TestProgressCallback:
    """Progress callback is forwarded to atomic_copy."""

    def test_video_progress_callback_passed(self, video_proc, tmp_path):
        """Callback argument is forwarded (we just verify it doesn't crash)."""
        src = _make_file(tmp_path, "v.mp4", b"data")
        dst = str(tmp_path / "out" / "v.mp4")
        calls: list[tuple[int, int]] = []
        result = video_proc.process(src, dst, lambda a, b: calls.append((a, b)))
        assert result.success is True

    def test_audio_progress_callback_passed(self, audio_proc, tmp_path):
        src = _make_file(tmp_path, "a.mp3", b"data")
        dst = str(tmp_path / "out" / "a.mp3")
        result = audio_proc.process(src, dst, lambda a, b: None)
        assert result.success is True

    def test_document_progress_callback_passed(self, doc_proc, tmp_path):
        src = _make_file(tmp_path, "d.pdf", b"data")
        dst = str(tmp_path / "out" / "d.pdf")
        result = doc_proc.process(src, dst, lambda a, b: None)
        assert result.success is True
