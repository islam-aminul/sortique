"""Session lifecycle management with state-machine enforcement."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from sortique.constants import FileStatus, SessionState
from sortique.data.config_manager import ConfigManager
from sortique.data.database import Database
from sortique.data.lock_manager import LockManager
from sortique.data.models import Session


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class InvalidTransitionError(Exception):
    """Raised when a session state transition violates the state machine."""


# ---------------------------------------------------------------------------
# Session manager
# ---------------------------------------------------------------------------

class SessionManager:
    """Manages the full session lifecycle with state-machine enforcement.

    Every state has a defined set of legal successor states.  Attempting an
    illegal transition raises :class:`InvalidTransitionError`.
    """

    VALID_TRANSITIONS: dict[SessionState, set[SessionState]] = {
        SessionState.PENDING: {SessionState.IN_PROGRESS, SessionState.STOPPED},
        SessionState.IN_PROGRESS: {SessionState.RUNNING, SessionState.STOPPED},
        SessionState.RUNNING: {
            SessionState.PAUSED,
            SessionState.COMPLETED,
            SessionState.STOPPED,
            SessionState.ERROR,
        },
        SessionState.PAUSED: {SessionState.RUNNING, SessionState.STOPPED},
        SessionState.COMPLETED: {SessionState.UNDONE},
        SessionState.STOPPED: {SessionState.RUNNING},   # resume
        SessionState.ERROR: {SessionState.RUNNING},      # retry
        SessionState.UNDONE: set(),                       # terminal
    }

    def __init__(self, db: Database, config: ConfigManager) -> None:
        self.db = db
        self.config = config

    # ------------------------------------------------------------------
    # Session creation
    # ------------------------------------------------------------------

    def create_session(
        self,
        source_dirs: list[str],
        destination_dir: str,
    ) -> Session:
        """Create a new session in ``PENDING`` state.

        A snapshot of the current configuration is captured so that the
        session's behaviour is reproducible even if global settings change
        later.
        """
        session = Session(
            source_dirs=source_dirs,
            destination_dir=destination_dir,
            config_snapshot=self.config.snapshot(),
        )
        self.db.create_session(session)
        return session

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def transition(
        self,
        session_id: str,
        new_state: SessionState,
    ) -> Session:
        """Move the session to *new_state*.

        Raises
        ------
        ValueError
            If the session does not exist.
        InvalidTransitionError
            If the transition is not allowed by the state machine.
        """
        session = self.db.get_session(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")

        allowed = self.VALID_TRANSITIONS.get(session.state, set())
        if new_state not in allowed:
            raise InvalidTransitionError(
                f"Cannot transition from {session.state.value} to {new_state.value}"
            )

        session.state = new_state
        session.updated_at = datetime.now(timezone.utc)
        self.db.update_session(session)
        return session

    # ------------------------------------------------------------------
    # Resumable session lookup
    # ------------------------------------------------------------------

    def get_resumable_session(
        self, destination_dir: str,
    ) -> Session | None:
        """Find the most recent interrupted session for *destination_dir*.

        A session is considered resumable when its state is ``STOPPED``
        (user-interrupted) or ``ERROR`` (crashed) and it targets the given
        destination directory.  Returns ``None`` when no match is found.
        """
        resumable_states = {SessionState.STOPPED, SessionState.ERROR}
        for session in self.db.list_sessions():
            if (
                session.destination_dir == destination_dir
                and session.state in resumable_states
            ):
                return session
        return None

    # ------------------------------------------------------------------
    # Live stats
    # ------------------------------------------------------------------

    def get_session_stats(self, session_id: str) -> dict:
        """Compute live statistics from the file records in the database.

        Returns a dict with keys ``files_processed``, ``files_skipped``,
        ``files_errored``, ``dupes_found``, and ``space_saved``.
        """
        all_records = self.db.get_file_records(session_id)

        processed = sum(
            1 for r in all_records if r.status == FileStatus.COMPLETED
        )
        skipped = sum(
            1 for r in all_records if r.status == FileStatus.SKIPPED
        )
        errored = sum(
            1 for r in all_records if r.status == FileStatus.ERROR
        )

        dup_groups = self.db.get_duplicate_groups(session_id)
        dupes = len(dup_groups)
        space_saved = sum(g.bytes_saved for g in dup_groups)

        return {
            "files_processed": processed,
            "files_skipped": skipped,
            "files_errored": errored,
            "dupes_found": dupes,
            "space_saved": space_saved,
        }

    # ------------------------------------------------------------------
    # Finalization
    # ------------------------------------------------------------------

    def finalize_session(self, session_id: str) -> None:
        """Mark the session ``COMPLETED``, persist final stats, and release the lock.

        Raises
        ------
        ValueError
            If the session does not exist.
        InvalidTransitionError
            If the session is not in a state that can transition to
            ``COMPLETED``.
        """
        session = self.db.get_session(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")

        # Validate the transition *before* doing any work.
        allowed = self.VALID_TRANSITIONS.get(session.state, set())
        if SessionState.COMPLETED not in allowed:
            raise InvalidTransitionError(
                f"Cannot finalize session in {session.state.value} state"
            )

        # Compute and store final stats.
        session.stats = self.get_session_stats(session_id)
        session.state = SessionState.COMPLETED
        session.updated_at = datetime.now(timezone.utc)
        self.db.update_session(session)

        # Release the destination-directory lock if we own it.
        lock = LockManager(session.destination_dir)
        info = lock.get_lock_info()
        if info is not None and info.get("pid") == os.getpid():
            try:
                os.unlink(lock.lock_path)
            except FileNotFoundError:
                pass
