"""Integration tests for the 13-stage Pipeline with real engine modules."""

from __future__ import annotations

import os
import struct
from pathlib import Path

import pytest
from PIL import Image

from sortique.constants import FileStatus, FileType
from sortique.data.config_manager import ConfigManager
from sortique.data.database import Database
from sortique.data.models import FileRecord, Session
from sortique.engine.categorizer import Categorizer
from sortique.engine.dedup import DedupEngine
from sortique.engine.detector import ContentDetector
from sortique.engine.hasher import FileHasher
from sortique.engine.metadata.audio_metadata import AudioMetadataExtractor
from sortique.engine.metadata.date_parser import DateParser
from sortique.engine.metadata.exif_extractor import ExifExtractor
from sortique.engine.metadata.video_metadata import VideoMetadataExtractor
from sortique.engine.path_generator import PathGenerator
from sortique.engine.processors.audio_processor import AudioProcessor
from sortique.engine.processors.document_processor import DocumentProcessor
from sortique.engine.processors.image_processor import ImageProcessor
from sortique.engine.processors.video_processor import VideoProcessor
from sortique.service.pipeline import Pipeline, PipelineStage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def workspace(tmp_path: Path):
    """Create a temp workspace with source and destination directories."""
    source = tmp_path / "source"
    dest = tmp_path / "dest"
    source.mkdir()
    dest.mkdir()
    return tmp_path, source, dest


@pytest.fixture()
def config(tmp_path: Path):
    """ConfigManager backed by a temp directory."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    return ConfigManager(config_dir=str(cfg_dir))


@pytest.fixture()
def db(tmp_path: Path):
    """Real SQLite database in temp dir."""
    return Database(str(tmp_path / "test.db"))


@pytest.fixture()
def session(db: Database):
    """Create and return a session."""
    s = Session(source_dirs=["/src"], destination_dir="/dst")
    db.create_session(s)
    return s


@pytest.fixture()
def pipeline_factory(db, config):
    """Factory to create a Pipeline with all real engine modules."""

    def _make(session_id: str, dest_root: str, *, dry_run: bool = False):
        hasher = FileHasher()
        return Pipeline(
            db=db,
            session_id=session_id,
            detector=ContentDetector(),
            hasher=hasher,
            dedup=DedupEngine(db, hasher),
            categorizer=Categorizer(config),
            path_generator=PathGenerator(config, dest_root),
            exif_extractor=ExifExtractor(),
            date_parser=DateParser(config),
            video_extractor=VideoMetadataExtractor(),
            audio_extractor=AudioMetadataExtractor(),
            image_processor=ImageProcessor(config),
            video_processor=VideoProcessor(config),
            audio_processor=AudioProcessor(config),
            document_processor=DocumentProcessor(),
            dry_run=dry_run,
        )

    return _make


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_jpeg(path: Path, width: int = 100, height: int = 80) -> None:
    """Write a minimal valid JPEG file."""
    img = Image.new("RGB", (width, height), color=(255, 0, 0))
    img.save(str(path), format="JPEG")


def _create_text_file(path: Path, content: str = "Hello world") -> None:
    path.write_text(content, encoding="utf-8")


def _create_unknown_file(path: Path) -> None:
    """Write a file with unrecognised magic bytes and extension."""
    path.write_bytes(b"\x00\x01\x02\x03UNKNOWN_DATA")


def _make_record(
    db: Database, session_id: str, source_path: str, source_dir: str,
) -> FileRecord:
    """Create and persist a FileRecord at pipeline_stage=1."""
    rec = FileRecord(
        session_id=session_id,
        source_path=source_path,
        source_dir=source_dir,
    )
    db.create_file_record(rec)
    return rec


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

class TestPipelineJpeg:
    """A JPEG file should be detected, categorised, and copied."""

    def test_jpeg_full_pipeline(self, workspace, db, session, config, pipeline_factory):
        tmp_path, source, dest = workspace
        jpeg_path = source / "photo.jpg"
        _create_jpeg(jpeg_path)

        rec = _make_record(db, session.id, str(jpeg_path), str(source))
        pipe = pipeline_factory(session.id, str(dest))

        result = pipe.process_file(rec)

        assert result.final_status == FileStatus.COMPLETED
        assert result.stages_completed == PipelineStage.VERIFY
        assert rec.file_type == FileType.IMAGE
        assert rec.content_type == "image/jpeg"
        assert rec.sha256_hash is not None
        assert rec.category != ""
        assert rec.destination_path is not None
        assert os.path.exists(rec.destination_path)
        assert rec.verified is True


class TestPipelineTextDocument:
    """A .txt file should be detected as DOCUMENT and categorised."""

    def test_txt_full_pipeline(self, workspace, db, session, config, pipeline_factory):
        tmp_path, source, dest = workspace
        txt_path = source / "notes.txt"
        _create_text_file(txt_path)

        rec = _make_record(db, session.id, str(txt_path), str(source))
        pipe = pipeline_factory(session.id, str(dest))

        result = pipe.process_file(rec)

        assert result.final_status == FileStatus.COMPLETED
        assert rec.file_type == FileType.DOCUMENT
        assert rec.content_type == "text/plain"
        assert rec.category == "Documents/Text"
        assert rec.destination_path is not None
        assert os.path.exists(rec.destination_path)


class TestPipelineUnknownFile:
    """An unrecognised file should be skipped at the unknown-filter stage."""

    def test_unknown_file_skipped(self, workspace, db, session, config, pipeline_factory):
        tmp_path, source, dest = workspace
        unk_path = source / "mystery.xyz"
        _create_unknown_file(unk_path)

        rec = _make_record(db, session.id, str(unk_path), str(source))
        pipe = pipeline_factory(session.id, str(dest))

        result = pipe.process_file(rec)

        assert result.final_status == FileStatus.SKIPPED
        assert result.skip_reason == "unknown file type"
        assert rec.file_type == FileType.UNKNOWN
        # Should have stopped at stage 5 (HASH, which does the unknown filter)
        assert result.stages_completed == PipelineStage.HASH


class TestPipelineBatch:
    """Process a batch of mixed files: JPEG, text, and unknown."""

    def test_batch_mixed_files(self, workspace, db, session, config, pipeline_factory):
        tmp_path, source, dest = workspace

        jpeg_path = source / "IMG_0001.jpg"
        _create_jpeg(jpeg_path)

        txt_path = source / "readme.txt"
        _create_text_file(txt_path)

        unk_path = source / "data.zzz"
        _create_unknown_file(unk_path)

        records = [
            _make_record(db, session.id, str(jpeg_path), str(source)),
            _make_record(db, session.id, str(txt_path), str(source)),
            _make_record(db, session.id, str(unk_path), str(source)),
        ]

        pipe = pipeline_factory(session.id, str(dest))
        results = pipe.process_batch(records)

        assert len(results) == 3

        # JPEG: completed
        assert results[0].final_status == FileStatus.COMPLETED
        assert records[0].file_type == FileType.IMAGE

        # TXT: completed
        assert results[1].final_status == FileStatus.COMPLETED
        assert records[1].file_type == FileType.DOCUMENT

        # Unknown: skipped
        assert results[2].final_status == FileStatus.SKIPPED
        assert results[2].skip_reason == "unknown file type"


class TestPipelineDryRun:
    """In dry-run mode, no files should be copied."""

    def test_dry_run_no_copy(self, workspace, db, session, config, pipeline_factory):
        tmp_path, source, dest = workspace
        jpeg_path = source / "photo.jpg"
        _create_jpeg(jpeg_path)

        rec = _make_record(db, session.id, str(jpeg_path), str(source))
        pipe = pipeline_factory(session.id, str(dest), dry_run=True)

        result = pipe.process_file(rec)

        assert result.final_status == FileStatus.COMPLETED
        assert rec.destination_path is not None
        # File should NOT have been copied in dry-run mode
        assert not os.path.exists(rec.destination_path)


class TestPipelineDuplicate:
    """Two identical files should result in the second being skipped as a dup."""

    def test_duplicate_detection(self, workspace, db, session, config, pipeline_factory):
        tmp_path, source, dest = workspace

        # Create two identical JPEG files.
        jpeg1 = source / "photo_a.jpg"
        _create_jpeg(jpeg1)

        jpeg2 = source / "photo_b.jpg"
        # Copy the bytes exactly so the SHA-256 matches.
        jpeg2.write_bytes(jpeg1.read_bytes())

        rec1 = _make_record(db, session.id, str(jpeg1), str(source))
        rec2 = _make_record(db, session.id, str(jpeg2), str(source))

        pipe = pipeline_factory(session.id, str(dest))

        r1 = pipe.process_file(rec1)
        r2 = pipe.process_file(rec2)

        assert r1.final_status == FileStatus.COMPLETED
        assert r2.final_status == FileStatus.SKIPPED
        assert r2.skip_reason == "exact duplicate"


class TestPipelineHiddenFile:
    """A hidden file (dot-prefix) should be skipped at pattern-skip stage."""

    def test_hidden_file_skipped(self, workspace, db, session, config, pipeline_factory):
        tmp_path, source, dest = workspace
        hidden = source / ".hidden_file.jpg"
        _create_jpeg(hidden)

        rec = _make_record(db, session.id, str(hidden), str(source))
        pipe = pipeline_factory(session.id, str(dest))

        result = pipe.process_file(rec)

        assert result.final_status == FileStatus.SKIPPED
        assert result.skip_reason == "hidden or system file"
        assert result.stages_completed == PipelineStage.PATTERN_SKIP


class TestPipelineMissingFile:
    """A non-existent source file should be skipped at init."""

    def test_missing_source(self, workspace, db, session, config, pipeline_factory):
        tmp_path, source, dest = workspace
        missing = str(source / "nonexistent.jpg")

        rec = _make_record(db, session.id, missing, str(source))
        pipe = pipeline_factory(session.id, str(dest))

        result = pipe.process_file(rec)

        assert result.final_status == FileStatus.SKIPPED
        assert "file not found" in result.skip_reason
