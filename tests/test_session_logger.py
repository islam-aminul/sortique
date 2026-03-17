"""Tests for SessionLogger."""

from __future__ import annotations

import os
import re
import threading
from unittest.mock import MagicMock

import pytest

from sortique.constants import FileStatus
from sortique.data.models import FileRecord
from sortique.service.pipeline import PipelineResult
from sortique.service.session_logger import SessionLogger
from sortique.service.thread_pool import ProcessingProgress


@pytest.fixture()
def dest_dir(tmp_path):
    return str(tmp_path / "destination")


@pytest.fixture()
def source_dirs():
    return ["/src/photos", "/src/downloads"]


@pytest.fixture()
def logger(dest_dir, source_dirs):
    lg = SessionLogger(dest_dir, source_dirs)
    yield lg
    if not lg._file.closed:
        lg.close()


class TestLogFileCreation:
    def test_creates_logs_directory(self, logger, dest_dir):
        assert os.path.isdir(os.path.join(dest_dir, "logs"))

    def test_creates_log_file(self, logger):
        assert os.path.isfile(logger.log_path)

    def test_log_filename_format(self, logger):
        filename = os.path.basename(logger.log_path)
        assert re.match(r"\d{8}-\d{4}\.log$", filename)

    def test_log_path_under_destination(self, logger, dest_dir):
        assert logger.log_path.startswith(os.path.join(dest_dir, "logs"))


class TestHeader:
    def test_header_written(self, logger, dest_dir, source_dirs):
        logger.close()
        content = open(logger.log_path, encoding="utf-8").read()
        assert "# Sortique Session Log" in content
        assert "# Source: /src/photos, /src/downloads" in content
        assert f"# Destination: {dest_dir}" in content
        assert "# Started:" in content


class TestLogFile:
    def _make_record(self, **kwargs) -> FileRecord:
        defaults = dict(source_path="/src/photos/test.jpg", destination_path=None)
        defaults.update(kwargs)
        return FileRecord(**defaults)

    def _make_result(self, **kwargs) -> PipelineResult:
        defaults = dict(final_status=FileStatus.COMPLETED)
        defaults.update(kwargs)
        return PipelineResult(**defaults)

    def test_log_completed_file(self, logger):
        record = self._make_record(
            destination_path="/dest/Images/2024/01/test.jpg",
        )
        result = self._make_result(final_status=FileStatus.COMPLETED)
        logger.log_file(record, result)
        logger.close()

        content = open(logger.log_path, encoding="utf-8").read()
        lines = content.strip().split("\n")
        last = lines[-1]
        assert "COMPLETED" in last
        assert "/src/photos/test.jpg" in last
        assert "/dest/Images/2024/01/test.jpg" in last

    def test_log_skipped_file(self, logger):
        record = self._make_record()
        result = self._make_result(
            final_status=FileStatus.SKIPPED,
            skip_reason="exact duplicate",
        )
        logger.log_file(record, result)
        logger.close()

        content = open(logger.log_path, encoding="utf-8").read()
        lines = content.strip().split("\n")
        last = lines[-1]
        assert "SKIPPED" in last
        assert "exact duplicate" in last

    def test_log_error_file(self, logger):
        record = self._make_record()
        result = self._make_result(
            final_status=FileStatus.ERROR,
            error_message="PermissionError: Permission denied",
        )
        logger.log_file(record, result)
        logger.close()

        content = open(logger.log_path, encoding="utf-8").read()
        lines = content.strip().split("\n")
        last = lines[-1]
        assert "ERROR" in last
        assert "PermissionError: Permission denied" in last

    def test_log_multiple_files(self, logger):
        for i in range(5):
            record = self._make_record(
                source_path=f"/src/file_{i}.jpg",
                destination_path=f"/dest/file_{i}.jpg",
            )
            result = self._make_result()
            logger.log_file(record, result)
        logger.close()

        content = open(logger.log_path, encoding="utf-8").read()
        lines = [l for l in content.split("\n") if l and not l.startswith("#")]
        assert len(lines) == 5


class TestSummary:
    def test_write_summary(self, logger):
        progress = ProcessingProgress(
            total_files=100,
            processed=100,
            skipped=15,
            errors=3,
            elapsed_seconds=42.5,
        )
        logger.write_summary(progress)
        logger.close()

        content = open(logger.log_path, encoding="utf-8").read()
        assert "Completed: 82" in content
        assert "Skipped: 15" in content
        assert "Errors: 3" in content
        assert "Duration: 42.5s" in content


class TestThreadSafety:
    def test_concurrent_writes(self, logger):
        """Multiple threads writing concurrently produce correct number of lines."""
        num_threads = 10
        writes_per_thread = 50

        def _write(thread_id):
            for i in range(writes_per_thread):
                record = FileRecord(
                    source_path=f"/src/t{thread_id}_f{i}.jpg",
                    destination_path=f"/dest/t{thread_id}_f{i}.jpg",
                )
                result = PipelineResult(final_status=FileStatus.COMPLETED)
                logger.log_file(record, result)

        threads = [
            threading.Thread(target=_write, args=(t,))
            for t in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        logger.close()
        content = open(logger.log_path, encoding="utf-8").read()
        data_lines = [l for l in content.split("\n") if l and not l.startswith("#")]
        assert len(data_lines) == num_threads * writes_per_thread


class TestClose:
    def test_close_flushes_and_closes(self, logger):
        logger.close()
        assert logger._file.closed

    def test_double_close_safe(self, logger):
        logger.close()
        logger.close()  # should not raise
