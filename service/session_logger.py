"""Thread-safe operational log writer for a single Sortique session."""

from __future__ import annotations

import os
import threading
from datetime import datetime
from typing import TYPE_CHECKING, TextIO

from sortique.constants import FileStatus

if TYPE_CHECKING:
    from sortique.data.models import FileRecord
    from sortique.service.pipeline import PipelineResult
    from sortique.service.thread_pool import ProcessingProgress

_TIMESTAMP_FMT = "%Y-%m-%d %H:%M:%S"
_FILENAME_FMT = "%Y%m%d-%H%M"

# Status labels, padded for alignment.
_STATUS_LABELS: dict[FileStatus, str] = {
    FileStatus.COMPLETED: "COMPLETED",
    FileStatus.SKIPPED:   "SKIPPED  ",
    FileStatus.ERROR:     "ERROR    ",
}


class SessionLogger:
    """Writes one line per processed file to ``<destination>/logs/YYYYMMDD-HHMM.log``.

    All public methods are thread-safe.
    """

    def __init__(
        self,
        destination_dir: str,
        source_dirs: list[str],
    ) -> None:
        self._lock = threading.Lock()
        self._start_time = datetime.now()

        logs_dir = os.path.join(destination_dir, "logs")
        os.makedirs(logs_dir, exist_ok=True)

        filename = self._start_time.strftime(_FILENAME_FMT) + ".log"
        self._log_path = os.path.join(logs_dir, filename)

        self._file: TextIO = open(self._log_path, "w", encoding="utf-8")  # noqa: SIM115
        self._write_header(destination_dir, source_dirs)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def log_path(self) -> str:
        """Absolute path to the log file."""
        return self._log_path

    def log_file(
        self,
        record: FileRecord,
        result: PipelineResult,
    ) -> None:
        """Write a single log entry. Thread-safe."""
        timestamp = datetime.now().strftime(_TIMESTAMP_FMT)
        status_label = _STATUS_LABELS.get(result.final_status, str(result.final_status.value).upper().ljust(9))

        if result.final_status == FileStatus.COMPLETED:
            detail = record.destination_path or ""
        elif result.final_status == FileStatus.SKIPPED:
            detail = result.skip_reason or ""
        elif result.final_status == FileStatus.ERROR:
            detail = result.error_message or ""
        else:
            detail = ""

        line = f"{timestamp} | {status_label} | {record.source_path} | {detail}\n"

        with self._lock:
            self._file.write(line)
            self._file.flush()

    def write_summary(self, progress: ProcessingProgress) -> None:
        """Write a summary footer at session end."""
        completed = progress.processed - progress.skipped - progress.errors
        line = (
            f"#\n"
            f"# Completed: {completed}"
            f" | Skipped: {progress.skipped}"
            f" | Errors: {progress.errors}"
            f" | Duration: {progress.elapsed_seconds:.1f}s\n"
        )
        with self._lock:
            self._file.write(line)
            self._file.flush()

    def close(self) -> None:
        """Flush and close the log file."""
        with self._lock:
            if not self._file.closed:
                self._file.flush()
                self._file.close()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _write_header(
        self,
        destination_dir: str,
        source_dirs: list[str],
    ) -> None:
        sources = ", ".join(source_dirs)
        header = (
            f"# Sortique Session Log\n"
            f"# Started: {self._start_time.strftime(_TIMESTAMP_FMT)}\n"
            f"# Source: {sources}\n"
            f"# Destination: {destination_dir}\n"
            f"#\n"
        )
        self._file.write(header)
        self._file.flush()
