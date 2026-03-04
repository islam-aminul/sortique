"""Tests for sortique.service.pipeline."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from sortique.constants import FileStatus, FileType
from sortique.data.database import Database
from sortique.data.models import FileRecord, Session
from sortique.service.pipeline import Pipeline, PipelineResult, PipelineStage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path):
    """Fresh SQLite database in a temp directory."""
    db_path = str(tmp_path / "test.db")
    database = Database(db_path)
    yield database
    database.close()


@pytest.fixture()
def session(db):
    """Persisted test session."""
    sess = Session(
        source_dirs=["/tmp/photos"],
        destination_dir="/tmp/organized",
    )
    db.create_session(sess)
    return sess


@pytest.fixture()
def sample_file(tmp_path):
    """Real file on disk (minimal JPEG header + padding)."""
    f = tmp_path / "photo.jpg"
    f.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
    return str(f)


def _make_record(session_id: str, source_path: str, **overrides) -> FileRecord:
    """Create a ``FileRecord`` with sensible defaults."""
    kwargs: dict = dict(
        session_id=session_id,
        source_path=source_path,
        source_dir=os.path.dirname(source_path),
        file_type=FileType.IMAGE,
        status=FileStatus.PENDING,
        pipeline_stage=1,
    )
    kwargs.update(overrides)
    return FileRecord(**kwargs)


def _mock_pipeline(db, session_id, *, dry_run=False):
    """Create a Pipeline with MagicMock engine dependencies.

    Mocks are configured to return sensible defaults so the pipeline
    framework tests (resume, error isolation, skip, dry-run) can run
    through stages without real engine logic.
    """
    from sortique.engine.dedup import DedupResult
    from sortique.engine.metadata.date_parser import DateResult
    from sortique.engine.metadata.exif_extractor import ExifResult

    detector = MagicMock()
    detector.detect.return_value = ("image/jpeg", FileType.IMAGE)

    hasher = MagicMock()
    hasher.hash_file.return_value = "abc123"

    dedup = MagicMock()
    dedup.check_duplicate.return_value = DedupResult(
        is_duplicate=False, original_file_id=None,
        duplicate_group_id=None, bytes_saved=0,
    )

    exif_extractor = MagicMock()
    exif_extractor.extract.return_value = ExifResult()

    date_parser = MagicMock()
    date_parser.extract_date.return_value = DateResult()

    categorizer = MagicMock()
    categorizer.categorize_image.return_value = "Collection"
    categorizer.categorize_video.return_value = "Originals/Unknown"
    categorizer.categorize_audio.return_value = "Collection"
    categorizer.categorize_document.return_value = "Documents/Other"

    path_generator = MagicMock()
    path_generator.generate.return_value = "/tmp/dest/file.jpg"
    path_generator.resolve_conflict.return_value = "/tmp/dest/file.jpg"

    image_processor = MagicMock()
    image_processor.copy_original.return_value = True

    video_processor = MagicMock()
    audio_processor = MagicMock()
    document_processor = MagicMock()

    return Pipeline(
        db=db,
        session_id=session_id,
        detector=detector,
        hasher=hasher,
        dedup=dedup,
        categorizer=categorizer,
        path_generator=path_generator,
        exif_extractor=exif_extractor,
        date_parser=date_parser,
        video_extractor=MagicMock(),
        audio_extractor=MagicMock(),
        image_processor=image_processor,
        video_processor=video_processor,
        audio_processor=audio_processor,
        document_processor=document_processor,
        dry_run=dry_run,
    )


# ===================================================================
# 1. Full pipeline run
# ===================================================================


class TestFullPipelineRun:
    """A normal file should transit all 13 stages and end as COMPLETED."""

    def test_simple_file_completes_all_stages(self, db, session, sample_file):
        record = _make_record(session.id, sample_file)
        db.create_file_record(record)

        result = _mock_pipeline(db, session.id).process_file(record)

        assert result.file_id == record.id
        assert result.final_status == FileStatus.COMPLETED
        assert result.stages_completed == PipelineStage.VERIFY
        assert result.skip_reason is None
        assert result.error_message is None

    def test_file_size_recorded_in_init(self, db, session, sample_file):
        record = _make_record(session.id, sample_file)
        db.create_file_record(record)

        _mock_pipeline(db, session.id).process_file(record)

        assert record.file_size == os.path.getsize(sample_file)

    def test_nonexistent_file_skipped_at_init(self, db, session):
        record = _make_record(session.id, "/nonexistent/file.jpg")
        db.create_file_record(record)

        result = _mock_pipeline(db, session.id).process_file(record)

        assert result.final_status == FileStatus.SKIPPED
        assert "file not found" in result.skip_reason
        assert result.stages_completed == PipelineStage.INIT

    def test_db_record_updated_on_completion(self, db, session, sample_file):
        record = _make_record(session.id, sample_file)
        db.create_file_record(record)

        _mock_pipeline(db, session.id).process_file(record)

        rows = db.get_file_records(session.id, status=FileStatus.COMPLETED)
        assert len(rows) == 1
        assert rows[0].id == record.id

    def test_batch_processes_all_files(self, db, session, tmp_path):
        paths = []
        for i in range(3):
            p = tmp_path / f"img_{i}.jpg"
            p.write_bytes(b"\xff\xd8\xff\xe0" + bytes([i]) * 50)
            paths.append(str(p))

        records = [_make_record(session.id, p) for p in paths]
        for r in records:
            db.create_file_record(r)

        results = _mock_pipeline(db, session.id).process_batch(records)

        assert len(results) == 3
        assert all(r.final_status == FileStatus.COMPLETED for r in results)


# ===================================================================
# 2. Resume from an intermediate stage
# ===================================================================


class TestResume:
    """Files with ``pipeline_stage > 1`` should skip earlier stages."""

    def test_resume_from_stage_5(self, db, session, sample_file):
        record = _make_record(session.id, sample_file, pipeline_stage=5)
        db.create_file_record(record)

        result = _mock_pipeline(db, session.id).process_file(record)

        assert result.final_status == FileStatus.COMPLETED
        assert result.stages_completed == PipelineStage.VERIFY

    def test_resume_skips_init_checks(self, db, session, sample_file):
        """Starting from stage 5 means stages 1-4 are not executed."""
        record = _make_record(session.id, sample_file, pipeline_stage=5)
        db.create_file_record(record)

        pipeline = _mock_pipeline(db, session.id)

        # Patch _stage_init to explode — it must never be called.
        def _should_not_run(rec):
            raise AssertionError("_stage_init should not be called on resume")

        pipeline._stage_init = _should_not_run

        result = pipeline.process_file(record)
        assert result.final_status == FileStatus.COMPLETED

    def test_resume_from_last_stage(self, db, session, sample_file):
        record = _make_record(session.id, sample_file, pipeline_stage=13)
        db.create_file_record(record)

        result = _mock_pipeline(db, session.id).process_file(record)

        assert result.final_status == FileStatus.COMPLETED
        assert result.stages_completed == PipelineStage.VERIFY


# ===================================================================
# 3. Error isolation
# ===================================================================


class TestErrorIsolation:
    """An error in one file must not prevent other files from processing."""

    def test_error_does_not_affect_other_files(self, db, session, sample_file):
        good = _make_record(session.id, sample_file)
        bad = _make_record(session.id, "/nonexistent/missing.jpg")
        db.create_file_record(good)
        db.create_file_record(bad)

        results = _mock_pipeline(db, session.id).process_batch([bad, good])

        assert results[0].final_status == FileStatus.SKIPPED  # missing file
        assert results[1].final_status == FileStatus.COMPLETED

    def test_exception_caught_and_recorded(self, db, session, sample_file):
        record = _make_record(session.id, sample_file)
        db.create_file_record(record)

        pipeline = _mock_pipeline(db, session.id)

        def _boom(rec):
            raise RuntimeError("stage exploded")

        pipeline._stage_detect = _boom
        result = pipeline.process_file(record)

        assert result.final_status == FileStatus.ERROR
        assert "stage exploded" in result.error_message
        # Stopped *before* DETECT, so last completed = PATTERN_SKIP
        assert result.stages_completed == PipelineStage.PATTERN_SKIP

    def test_error_persisted_to_db(self, db, session, sample_file):
        record = _make_record(session.id, sample_file)
        db.create_file_record(record)

        pipeline = _mock_pipeline(db, session.id)

        def _boom(rec):
            raise ValueError("bad data")

        pipeline._stage_hash = _boom
        pipeline.process_file(record)

        rows = db.get_file_records(session.id, status=FileStatus.ERROR)
        assert len(rows) == 1
        assert "bad data" in rows[0].error_message


# ===================================================================
# 4. Skip propagation
# ===================================================================


class TestSkipPropagation:
    """Files matching skip rules must short-circuit the pipeline."""

    def test_hidden_file_skipped(self, db, session, tmp_path):
        hidden = tmp_path / ".hidden_file.jpg"
        hidden.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

        record = _make_record(session.id, str(hidden))
        db.create_file_record(record)

        result = _mock_pipeline(db, session.id).process_file(record)

        assert result.final_status == FileStatus.SKIPPED
        assert "hidden or system" in result.skip_reason
        assert result.stages_completed == PipelineStage.PATTERN_SKIP

    def test_system_file_skipped(self, db, session, tmp_path):
        thumbs = tmp_path / "Thumbs.db"
        thumbs.write_bytes(b"\x00" * 50)

        record = _make_record(session.id, str(thumbs))
        db.create_file_record(record)

        result = _mock_pipeline(db, session.id).process_file(record)

        assert result.final_status == FileStatus.SKIPPED
        assert "hidden or system" in result.skip_reason

    def test_ds_store_skipped(self, db, session, tmp_path):
        ds = tmp_path / ".DS_Store"
        ds.write_bytes(b"\x00" * 50)

        record = _make_record(session.id, str(ds))
        db.create_file_record(record)

        result = _mock_pipeline(db, session.id).process_file(record)

        assert result.final_status == FileStatus.SKIPPED

    def test_already_processed_file_skipped(self, db, session, sample_file):
        # First record: already completed in this session.
        first = _make_record(
            session.id, sample_file,
            status=FileStatus.COMPLETED, pipeline_stage=13,
        )
        db.create_file_record(first)

        # Second record: same source_path, should be skipped.
        second = _make_record(session.id, sample_file)
        db.create_file_record(second)

        result = _mock_pipeline(db, session.id).process_file(second)

        assert result.final_status == FileStatus.SKIPPED
        assert "already processed" in result.skip_reason

    def test_skip_persisted_to_db(self, db, session, tmp_path):
        hidden = tmp_path / ".secret"
        hidden.write_bytes(b"data")

        record = _make_record(session.id, str(hidden))
        db.create_file_record(record)

        _mock_pipeline(db, session.id).process_file(record)

        rows = db.get_file_records(session.id, status=FileStatus.SKIPPED)
        assert len(rows) == 1
        assert rows[0].skip_reason == "hidden or system file"


# ===================================================================
# 5. Dry-run flag
# ===================================================================


class TestDryRun:
    """In dry-run mode the pipeline must not write to the database."""

    def test_dry_run_does_not_persist_completion(self, db, session, sample_file):
        record = _make_record(session.id, sample_file)
        db.create_file_record(record)

        result = _mock_pipeline(db, session.id, dry_run=True).process_file(record)

        assert result.final_status == FileStatus.COMPLETED

        # DB should still show original PENDING status.
        rows = db.get_file_records(session.id, status=FileStatus.PENDING)
        assert len(rows) == 1

    def test_dry_run_still_returns_full_result(self, db, session, sample_file):
        record = _make_record(session.id, sample_file)
        db.create_file_record(record)

        result = _mock_pipeline(db, session.id, dry_run=True).process_file(record)

        assert result.file_id == record.id
        assert result.stages_completed == PipelineStage.VERIFY

    def test_dry_run_skip_not_persisted(self, db, session, tmp_path):
        hidden = tmp_path / ".hidden"
        hidden.write_bytes(b"x")

        record = _make_record(session.id, str(hidden))
        db.create_file_record(record)

        result = _mock_pipeline(db, session.id, dry_run=True).process_file(record)

        assert result.final_status == FileStatus.SKIPPED

        # DB still shows PENDING.
        rows = db.get_file_records(session.id, status=FileStatus.PENDING)
        assert len(rows) == 1

    def test_dry_run_error_not_persisted(self, db, session, sample_file):
        record = _make_record(session.id, sample_file)
        db.create_file_record(record)

        pipeline = _mock_pipeline(db, session.id, dry_run=True)

        def _boom(rec):
            raise RuntimeError("test error")

        pipeline._stage_detect = _boom
        result = pipeline.process_file(record)

        assert result.final_status == FileStatus.ERROR

        # DB still shows PENDING.
        rows = db.get_file_records(session.id, status=FileStatus.PENDING)
        assert len(rows) == 1


# ===================================================================
# PipelineStage enum sanity
# ===================================================================


class TestPipelineStageEnum:

    def test_stage_values(self):
        assert PipelineStage.INIT == 1
        assert PipelineStage.VERIFY == 13
        assert len(PipelineStage) == 13

    def test_stages_monotonically_increasing(self):
        values = [s.value for s in PipelineStage]
        assert values == sorted(values)

    def test_all_stages_registered_in_pipeline(self):
        registered = [s for s, _ in Pipeline.STAGES]
        assert registered == list(PipelineStage)
