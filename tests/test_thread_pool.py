"""Tests for FileProcessorPool and ProcessingProgress."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest

from sortique.constants import DEFAULT_THREADS, MAX_THREADS, FileStatus
from sortique.data.models import FileRecord
from sortique.service.pipeline import PipelineResult
from sortique.service.thread_pool import FileProcessorPool, ProcessingProgress


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_records(n: int, session_id: str = "sess-1") -> list[FileRecord]:
    """Create *n* dummy FileRecords with ascending file sizes."""
    return [
        FileRecord(
            session_id=session_id,
            source_path=f"/src/file_{i}.jpg",
            source_dir="/src",
            file_size=1000 * (i + 1),
        )
        for i in range(n)
    ]


def _mock_pipeline(*, delay: float = 0.0, side_effect=None):
    """Create a mock Pipeline whose process_file returns COMPLETED."""
    pipeline = MagicMock()

    def _default_process(record):
        if delay:
            time.sleep(delay)
        return PipelineResult(
            file_id=record.id,
            final_status=FileStatus.COMPLETED,
            stages_completed=13,
        )

    if side_effect is not None:
        pipeline.process_file.side_effect = side_effect
    else:
        pipeline.process_file.side_effect = _default_process

    return pipeline


# ---------------------------------------------------------------------------
# ProcessingProgress dataclass
# ---------------------------------------------------------------------------

class TestProcessingProgress:
    def test_defaults(self):
        p = ProcessingProgress()
        assert p.total_files == 0
        assert p.processed == 0
        assert p.skipped == 0
        assert p.errors == 0
        assert p.duplicates == 0
        assert p.bytes_processed == 0
        assert p.bytes_saved == 0
        assert p.current_file is None
        assert p.elapsed_seconds == 0.0
        assert p.files_per_second == 0.0

    def test_custom_values(self):
        p = ProcessingProgress(
            total_files=100,
            processed=50,
            skipped=5,
            errors=2,
            duplicates=3,
            bytes_processed=50000,
            bytes_saved=3000,
            current_file="/test/file.jpg",
            elapsed_seconds=10.0,
            files_per_second=5.0,
        )
        assert p.total_files == 100
        assert p.processed == 50
        assert p.skipped == 5
        assert p.errors == 2
        assert p.duplicates == 3
        assert p.bytes_processed == 50000
        assert p.bytes_saved == 3000
        assert p.current_file == "/test/file.jpg"
        assert p.elapsed_seconds == 10.0
        assert p.files_per_second == 5.0


# ---------------------------------------------------------------------------
# Basic parallel processing
# ---------------------------------------------------------------------------

class TestBasicParallelProcessing:
    """Test basic parallel processing (10 files, 2 workers)."""

    def test_process_10_files_2_workers(self):
        records = _make_records(10)
        pipeline = _mock_pipeline()
        db = MagicMock()

        pool = FileProcessorPool(pipeline, db, num_workers=2)
        pool.start(records)
        progress = pool.wait()

        assert progress.processed == 10
        assert progress.total_files == 10
        assert progress.errors == 0
        assert progress.elapsed_seconds > 0
        assert not pool.is_running

    def test_all_files_processed_exactly_once(self):
        records = _make_records(10)
        processed_ids: list[str] = []
        lock = threading.Lock()

        def track_process(record):
            with lock:
                processed_ids.append(record.id)
            return PipelineResult(
                file_id=record.id,
                final_status=FileStatus.COMPLETED,
                stages_completed=13,
            )

        pipeline = _mock_pipeline(side_effect=track_process)
        db = MagicMock()

        pool = FileProcessorPool(pipeline, db, num_workers=2)
        pool.start(records)
        pool.wait()

        assert len(processed_ids) == 10
        assert set(processed_ids) == {r.id for r in records}

    def test_workers_capped_at_max_threads(self):
        pool = FileProcessorPool(MagicMock(), MagicMock(), num_workers=100)
        assert pool._num_workers == MAX_THREADS

    def test_workers_minimum_1(self):
        pool = FileProcessorPool(MagicMock(), MagicMock(), num_workers=0)
        assert pool._num_workers == 1

    def test_default_workers(self):
        pool = FileProcessorPool(MagicMock(), MagicMock())
        assert pool._num_workers == DEFAULT_THREADS

    def test_is_running_during_processing(self):
        records = _make_records(5)
        barrier = threading.Event()

        def blocking_process(record):
            barrier.wait(timeout=5.0)
            return PipelineResult(
                file_id=record.id,
                final_status=FileStatus.COMPLETED,
                stages_completed=13,
            )

        pipeline = _mock_pipeline(side_effect=blocking_process)
        db = MagicMock()

        pool = FileProcessorPool(pipeline, db, num_workers=1)
        pool.start(records)

        assert pool.is_running

        barrier.set()
        pool.wait()

        assert not pool.is_running

    def test_empty_records_list(self):
        pipeline = _mock_pipeline()
        db = MagicMock()

        pool = FileProcessorPool(pipeline, db, num_workers=2)
        pool.start([])
        progress = pool.wait()

        assert progress.processed == 0
        assert progress.total_files == 0


# ---------------------------------------------------------------------------
# Pause and resume
# ---------------------------------------------------------------------------

class TestPauseResume:
    """Test pause and resume behaviour."""

    def test_pause_blocks_workers(self):
        records = _make_records(20)
        process_count = [0]
        lock = threading.Lock()
        first_done = threading.Event()

        def counted_process(record):
            with lock:
                process_count[0] += 1
                count = process_count[0]
            if count == 1:
                first_done.set()
            return PipelineResult(
                file_id=record.id,
                final_status=FileStatus.COMPLETED,
                stages_completed=13,
            )

        pipeline = _mock_pipeline(side_effect=counted_process)
        db = MagicMock()

        pool = FileProcessorPool(pipeline, db, num_workers=1)
        pool.start(records)

        # Wait for the first file to complete.
        first_done.wait(timeout=5.0)

        # Pause.
        pool.pause()
        assert pool.is_paused

        # Give time for the worker to block on the pause event.
        time.sleep(0.3)
        count_at_pause = process_count[0]

        # While paused, count should not increase significantly.
        time.sleep(0.3)
        assert process_count[0] <= count_at_pause + 1  # at most one in-flight

        # Resume and let everything finish.
        pool.resume()
        assert not pool.is_paused
        progress = pool.wait()

        assert progress.processed == 20

    def test_resume_continues_processing(self):
        records = _make_records(5)
        pipeline = _mock_pipeline()
        db = MagicMock()

        pool = FileProcessorPool(pipeline, db, num_workers=1)
        pool.start(records)

        pool.pause()
        time.sleep(0.2)
        pool.resume()

        progress = pool.wait()
        assert progress.processed == 5

    def test_is_paused_false_when_not_running(self):
        pool = FileProcessorPool(MagicMock(), MagicMock())
        assert not pool.is_paused


# ---------------------------------------------------------------------------
# Stop
# ---------------------------------------------------------------------------

class TestStop:
    """Test stop — verify workers exit cleanly."""

    def test_stop_exits_cleanly(self):
        records = _make_records(100)
        processing_started = threading.Event()

        def slow_process(record):
            processing_started.set()
            time.sleep(0.05)
            return PipelineResult(
                file_id=record.id,
                final_status=FileStatus.COMPLETED,
                stages_completed=13,
            )

        pipeline = _mock_pipeline(side_effect=slow_process)
        db = MagicMock()

        pool = FileProcessorPool(pipeline, db, num_workers=2)
        pool.start(records)

        processing_started.wait(timeout=5.0)
        time.sleep(0.1)  # let a few files process
        pool.stop()
        progress = pool.wait()

        # Some files processed but not all 100.
        assert 0 < progress.processed < 100
        assert not pool.is_running

    def test_stop_while_paused(self):
        """Stop should unblock paused workers."""
        records = _make_records(10)
        first_done = threading.Event()

        def track_process(record):
            first_done.set()
            return PipelineResult(
                file_id=record.id,
                final_status=FileStatus.COMPLETED,
                stages_completed=13,
            )

        pipeline = _mock_pipeline(side_effect=track_process)
        db = MagicMock()

        pool = FileProcessorPool(pipeline, db, num_workers=1)
        pool.start(records)

        first_done.wait(timeout=5.0)
        pool.pause()
        time.sleep(0.2)
        pool.stop()

        progress = pool.wait()
        assert not pool.is_running
        # At least 1 processed, but likely not all.
        assert progress.processed >= 1

    def test_wait_returns_after_stop(self):
        """wait() should return promptly after stop()."""
        records = _make_records(50)

        def slow_process(record):
            time.sleep(0.1)
            return PipelineResult(
                file_id=record.id,
                final_status=FileStatus.COMPLETED,
                stages_completed=13,
            )

        pipeline = _mock_pipeline(side_effect=slow_process)
        db = MagicMock()

        pool = FileProcessorPool(pipeline, db, num_workers=1)
        pool.start(records)

        time.sleep(0.2)
        pool.stop()

        start = time.monotonic()
        pool.wait()
        wait_duration = time.monotonic() - start

        # wait() should return quickly (within the time for one file to finish).
        assert wait_duration < 2.0


# ---------------------------------------------------------------------------
# Progress aggregation
# ---------------------------------------------------------------------------

class TestProgressAggregation:
    """Test progress aggregation across mixed results."""

    def test_mixed_status_counts(self):
        records = _make_records(6)
        results = [
            PipelineResult(file_id="1", final_status=FileStatus.COMPLETED, stages_completed=13),
            PipelineResult(file_id="2", final_status=FileStatus.COMPLETED, stages_completed=13),
            PipelineResult(file_id="3", final_status=FileStatus.SKIPPED, skip_reason="hidden or system file", stages_completed=3),
            PipelineResult(file_id="4", final_status=FileStatus.SKIPPED, skip_reason="exact duplicate", stages_completed=6),
            PipelineResult(file_id="5", final_status=FileStatus.ERROR, error_message="boom", stages_completed=4),
            PipelineResult(file_id="6", final_status=FileStatus.COMPLETED, stages_completed=13),
        ]
        call_idx = [0]
        lock = threading.Lock()

        def process_with_results(record):
            with lock:
                idx = call_idx[0]
                call_idx[0] += 1
            return results[idx]

        pipeline = _mock_pipeline(side_effect=process_with_results)
        db = MagicMock()

        pool = FileProcessorPool(pipeline, db, num_workers=1)
        pool.start(records)
        progress = pool.wait()

        assert progress.processed == 6
        assert progress.skipped == 2
        assert progress.duplicates == 1
        assert progress.errors == 1
        assert progress.total_files == 6

    def test_bytes_processed_aggregation(self):
        # _make_records gives sizes 1000, 2000, 3000
        records = _make_records(3)
        pipeline = _mock_pipeline()
        db = MagicMock()

        pool = FileProcessorPool(pipeline, db, num_workers=1)
        pool.start(records)
        progress = pool.wait()

        assert progress.bytes_processed == 6000

    def test_bytes_saved_for_duplicates(self):
        # sizes: 1000, 2000
        records = _make_records(2)

        def dup_process(record):
            return PipelineResult(
                file_id=record.id,
                final_status=FileStatus.SKIPPED,
                skip_reason="exact duplicate",
                stages_completed=6,
            )

        pipeline = _mock_pipeline(side_effect=dup_process)
        db = MagicMock()

        pool = FileProcessorPool(pipeline, db, num_workers=1)
        pool.start(records)
        progress = pool.wait()

        assert progress.bytes_saved == 3000
        assert progress.duplicates == 2

    def test_progress_callback_called(self):
        records = _make_records(5)
        pipeline = _mock_pipeline()
        db = MagicMock()

        callback_counts: list[int] = []

        def on_progress(p: ProcessingProgress):
            callback_counts.append(p.processed)

        pool = FileProcessorPool(pipeline, db, num_workers=1)
        pool.start(records, progress_callback=on_progress)
        pool.wait()

        assert len(callback_counts) == 5
        assert callback_counts[-1] == 5

    def test_files_per_second_calculated(self):
        records = _make_records(3)
        pipeline = _mock_pipeline(delay=0.01)
        db = MagicMock()

        pool = FileProcessorPool(pipeline, db, num_workers=1)
        pool.start(records)
        progress = pool.wait()

        assert progress.files_per_second > 0
        assert progress.elapsed_seconds > 0

    def test_current_file_none_after_wait(self):
        records = _make_records(3)
        pipeline = _mock_pipeline()
        db = MagicMock()

        pool = FileProcessorPool(pipeline, db, num_workers=1)
        pool.start(records)
        progress = pool.wait()

        assert progress.current_file is None
