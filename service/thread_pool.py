"""Thread pool for parallel file processing."""

from __future__ import annotations

import logging
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from sortique.constants import DEFAULT_THREADS, FLUSH_INTERVAL, FileStatus, MAX_THREADS

if TYPE_CHECKING:
    from sortique.data.database import Database
    from sortique.data.models import FileRecord
    from sortique.service.pipeline import Pipeline

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Progress container
# ---------------------------------------------------------------------------

@dataclass
class ProcessingProgress:
    """Snapshot of parallel processing progress."""

    total_files: int = 0
    processed: int = 0
    skipped: int = 0
    errors: int = 0
    duplicates: int = 0
    bytes_processed: int = 0
    bytes_saved: int = 0
    current_file: str | None = None
    elapsed_seconds: float = 0.0
    files_per_second: float = 0.0


# ---------------------------------------------------------------------------
# Thread pool
# ---------------------------------------------------------------------------

class FileProcessorPool:
    """Parallel file processor with pause/resume and graceful stop.

    Uses a :class:`~concurrent.futures.ThreadPoolExecutor` to process files
    through the pipeline concurrently.  Workers pull files from a shared
    :class:`queue.Queue` and aggregate results into a single
    :class:`ProcessingProgress` instance protected by a lock.
    """

    def __init__(
        self,
        pipeline: Pipeline,
        db: Database,
        num_workers: int = DEFAULT_THREADS,
    ) -> None:
        self._pipeline = pipeline
        self._db = db
        self._num_workers = min(max(num_workers, 1), MAX_THREADS)

        self._paused = threading.Event()
        self._paused.set()  # starts unpaused
        self._stopped = threading.Event()

        self._lock = threading.Lock()
        self._progress = ProcessingProgress()

        self._executor: ThreadPoolExecutor | None = None
        self._futures: list = []
        self._running = False
        self._start_time: float = 0.0
        self._queue: queue.Queue = queue.Queue()
        self._progress_callback: Callable[[ProcessingProgress], None] | None = None
        self._flush_counter = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(
        self,
        records: list[FileRecord],
        *,
        progress_callback: Callable[[ProcessingProgress], None] | None = None,
    ) -> None:
        """Submit *records* for parallel processing."""
        self._running = True
        self._stopped.clear()
        self._paused.set()
        self._start_time = time.monotonic()
        self._progress = ProcessingProgress(total_files=len(records))
        self._progress_callback = progress_callback
        self._flush_counter = 0
        self._queue = queue.Queue()

        for rec in records:
            self._queue.put(rec)

        # Sentinel per worker to signal end of queue.
        for _ in range(self._num_workers):
            self._queue.put(None)

        self._executor = ThreadPoolExecutor(max_workers=self._num_workers)
        self._futures = [
            self._executor.submit(self._worker)
            for _ in range(self._num_workers)
        ]

    def pause(self) -> None:
        """Pause processing.  Workers finish their current file then block."""
        self._paused.clear()

    def resume(self) -> None:
        """Resume paused workers."""
        self._paused.set()

    def stop(self) -> None:
        """Graceful stop.  Workers finish their current file then exit."""
        self._stopped.set()
        self._paused.set()  # unblock any paused workers

    def wait(self) -> ProcessingProgress:
        """Block until all workers complete.  Returns final progress."""
        if self._executor is not None:
            for future in self._futures:
                future.result()
            self._executor.shutdown(wait=True)
            self._executor = None

        with self._lock:
            self._running = False
            elapsed = time.monotonic() - self._start_time
            self._progress.elapsed_seconds = elapsed
            if elapsed > 0 and self._progress.processed > 0:
                self._progress.files_per_second = (
                    self._progress.processed / elapsed
                )
            self._progress.current_file = None

        return self._progress

    @property
    def is_running(self) -> bool:
        """``True`` while workers are active."""
        return self._running

    @property
    def is_paused(self) -> bool:
        """``True`` if running and paused."""
        return self._running and not self._paused.is_set()

    # ------------------------------------------------------------------
    # Worker loop
    # ------------------------------------------------------------------

    def _worker(self) -> None:
        """Pull files from the queue, process, and aggregate progress."""
        while True:
            if self._stopped.is_set():
                break

            # Block while paused.
            self._paused.wait()

            if self._stopped.is_set():
                break

            try:
                record = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if record is None:  # sentinel
                break

            # Update current file.
            with self._lock:
                self._progress.current_file = record.source_path

            # --- process ---
            result = self._pipeline.process_file(record)

            # --- aggregate progress ---
            with self._lock:
                self._progress.processed += 1
                self._progress.bytes_processed += record.file_size

                if result.final_status == FileStatus.SKIPPED:
                    self._progress.skipped += 1
                    if result.skip_reason == "exact duplicate":
                        self._progress.duplicates += 1
                        self._progress.bytes_saved += record.file_size
                elif result.final_status == FileStatus.ERROR:
                    self._progress.errors += 1

                elapsed = time.monotonic() - self._start_time
                self._progress.elapsed_seconds = elapsed
                if elapsed > 0:
                    self._progress.files_per_second = (
                        self._progress.processed / elapsed
                    )

                self._flush_counter += 1

                if self._progress_callback is not None:
                    self._progress_callback(self._progress)

            # Periodic flush checkpoint.
            if self._flush_counter % FLUSH_INTERVAL == 0:
                logger.debug(
                    "Flush checkpoint: %d/%d files processed",
                    self._progress.processed,
                    self._progress.total_files,
                )
