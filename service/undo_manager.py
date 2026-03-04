"""Undo a completed session by removing destination files."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sortique.constants import FileStatus, SessionState
from sortique.data.file_system import FileSystemHelper

if TYPE_CHECKING:
    from sortique.data.database import Database
    from sortique.service.session_manager import SessionManager


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------

@dataclass
class UndoVerification:
    """Pre-flight check before an undo operation."""

    total_files: int = 0
    files_present: int = 0
    files_missing: int = 0
    bytes_to_free: int = 0
    safe_to_proceed: bool = True  # True if >90% of files still present


@dataclass
class UndoResult:
    """Outcome of an undo operation."""

    success: bool = False
    files_deleted: int = 0
    files_missing: int = 0
    folders_removed: int = 0
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class UndoManager:
    """Reverses a completed session by removing destination files."""

    # Minimum proportion of files that must still exist for a safe undo.
    SAFETY_THRESHOLD = 0.9

    def __init__(self, db: Database, session_manager: SessionManager) -> None:
        self.db = db
        self.session_manager = session_manager

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify(self, session_id: str) -> UndoVerification:
        """Dry-run verification before undo.

        Checks all destination files from the session:
        - Which files still exist at their destination?
        - Which have been moved or deleted since the session?
        - Total bytes that would be freed.

        Returns an :class:`UndoVerification` for user review.
        """
        records = self.db.get_file_records(
            session_id, status=FileStatus.COMPLETED,
        )

        total = 0
        present = 0
        missing = 0
        bytes_to_free = 0

        for rec in records:
            if not rec.destination_path:
                continue

            total += 1

            if os.path.exists(rec.destination_path):
                present += 1
                try:
                    bytes_to_free += os.path.getsize(rec.destination_path)
                except OSError:
                    pass
            else:
                missing += 1

        safe = total == 0 or (present / total) >= self.SAFETY_THRESHOLD

        return UndoVerification(
            total_files=total,
            files_present=present,
            files_missing=missing,
            bytes_to_free=bytes_to_free,
            safe_to_proceed=safe,
        )

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    def execute(
        self,
        session_id: str,
        force: bool = False,
    ) -> UndoResult:
        """Execute undo for a session.

        Steps:

        1. If not *force*: run :meth:`verify` first, abort if too many
           files are missing.
        2. Get all completed ``FileRecord``\\s for the session.
        3. Delete each destination file.
        4. Remove empty parent directories (up to destination root).
        5. Transition session state to ``UNDONE``.
        6. Return result with stats.
        """
        session = self.db.get_session(session_id)
        if session is None:
            return UndoResult(
                success=False,
                errors=[f"Session not found: {session_id}"],
            )

        # Pre-flight safety check.
        if not force:
            verification = self.verify(session_id)
            if not verification.safe_to_proceed:
                return UndoResult(
                    success=False,
                    files_missing=verification.files_missing,
                    errors=[
                        f"Too many files missing ({verification.files_missing}"
                        f"/{verification.total_files}). Use force=True to override."
                    ],
                )

        records = self.db.get_file_records(
            session_id, status=FileStatus.COMPLETED,
        )

        result = UndoResult()
        destination_root = session.destination_dir
        deleted_dirs: set[str] = set()

        for rec in records:
            if not rec.destination_path:
                continue

            if not os.path.exists(rec.destination_path):
                result.files_missing += 1
                continue

            if self._delete_file_safe(rec.destination_path):
                result.files_deleted += 1
                # Track parent directory for cleanup.
                deleted_dirs.add(os.path.dirname(rec.destination_path))
            else:
                result.errors.append(
                    f"Failed to delete: {rec.destination_path}"
                )

        # Remove empty parent directories.
        folders_removed = 0
        for dir_path in deleted_dirs:
            folders_removed += self._remove_empty_parents(
                dir_path, destination_root,
            )
        result.folders_removed = folders_removed

        # Transition session state.
        try:
            self.session_manager.transition(
                session_id, SessionState.UNDONE,
            )
        except Exception as exc:
            result.errors.append(f"State transition failed: {exc}")

        result.success = len(result.errors) == 0
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _delete_file_safe(filepath: str) -> bool:
        """Delete a file, return ``True`` if successful.  Never raises."""
        try:
            os.unlink(filepath)
            return True
        except OSError as exc:
            logger.warning("Could not delete %s: %s", filepath, exc)
            return False

    @staticmethod
    def _remove_empty_parents(path: str, stop_at: str) -> int:
        """Remove empty parent directories up to *stop_at*.

        Returns the number of directories removed.
        """
        count = 0
        stop = os.path.normpath(os.path.abspath(stop_at))
        current = os.path.normpath(os.path.abspath(path))

        while True:
            if current == stop or current == os.path.dirname(current):
                break

            if not current.startswith(stop):
                break

            try:
                os.rmdir(current)  # only succeeds when empty
                count += 1
            except OSError:
                break

            current = os.path.dirname(current)

        return count
