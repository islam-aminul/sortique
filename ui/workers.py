"""Background worker threads for long-running Sortique operations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QThread, Signal

if TYPE_CHECKING:
    from sortique.factory import AppFactory
    from sortique.data.models import FileRecord
    from sortique.engine.scanner import ScanResult
    from sortique.service.dry_run import DryRunSummary
    from sortique.service.thread_pool import ProcessingProgress
    from sortique.service.undo_manager import UndoResult


class ScanWorker(QThread):
    """Runs directory scanning in a background thread."""

    progress = Signal(int, str)   # files_found, current_path
    finished = Signal(object)     # ScanResult
    error = Signal(str)

    def __init__(
        self,
        factory: AppFactory,
        source_dirs: list[str],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._factory = factory
        self._source_dirs = source_dirs

    def run(self) -> None:
        try:
            scanner = self._factory.scanner()
            # Inject a progress callback that emits our signal.
            scanner.progress_callback = lambda count, path: self.progress.emit(
                count, path
            )
            result = scanner.scan(self._source_dirs)
            self.finished.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))
        finally:
            scanner = self._factory.scanner()
            scanner.progress_callback = None


class PipelineWorker(QThread):
    """Runs the file processing pipeline in a background thread."""

    progress = Signal(object)         # ProcessingProgress
    file_completed = Signal(str, str) # source_path, status
    finished = Signal(object)         # final ProcessingProgress
    error = Signal(str)

    def __init__(
        self,
        factory: AppFactory,
        records: list[FileRecord],
        destination_dir: str,
        session_id: str,
        source_dirs: list[str] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._factory = factory
        self._records = records
        self._destination_dir = destination_dir
        self._session_id = session_id
        self._source_dirs = source_dirs
        self._pool = None

    def run(self) -> None:
        try:
            pool = self._factory.thread_pool(self._destination_dir, self._source_dirs)
            # Patch the pipeline session_id before processing.
            pool._pipeline._session_id = self._session_id
            self._pool = pool

            def _on_progress(prog):
                self.progress.emit(prog)
                if prog.current_file:
                    self.file_completed.emit(
                        prog.current_file, "processing"
                    )

            pool.start(self._records, progress_callback=_on_progress)
            final = pool.wait()
            self.finished.emit(final)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))

    def pause(self) -> None:
        if self._pool is not None:
            self._pool.pause()

    def resume(self) -> None:
        if self._pool is not None:
            self._pool.resume()

    def stop(self) -> None:
        if self._pool is not None:
            self._pool.stop()


class DryRunWorker(QThread):
    """Runs a dry-run analysis in a background thread."""

    progress = Signal(int, int)  # current, total
    finished = Signal(object)    # DryRunSummary
    error = Signal(str)

    def __init__(
        self,
        factory: AppFactory,
        records: list[FileRecord],
        destination_dir: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._factory = factory
        self._records = records
        self._destination_dir = destination_dir

    def run(self) -> None:
        try:
            manager = self._factory.dry_run_manager(self._destination_dir)
            summary = manager.run(
                self._records,
                self._destination_dir,
                progress_callback=lambda cur, total: self.progress.emit(cur, total),
            )
            self.finished.emit(summary)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))


class UndoWorker(QThread):
    """Runs an undo operation in a background thread."""

    finished = Signal(object)  # UndoResult
    error = Signal(str)

    def __init__(
        self,
        factory: AppFactory,
        session_id: str,
        force: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._factory = factory
        self._session_id = session_id
        self._force = force

    def run(self) -> None:
        try:
            manager = self._factory.undo_manager()
            result = manager.execute(self._session_id, force=self._force)
            self.finished.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))
