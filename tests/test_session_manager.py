"""Tests for sortique.service.session_manager."""

from __future__ import annotations

import os

import pytest

from sortique.constants import (
    DupMatchType,
    FileStatus,
    FileType,
    SessionState,
)
from sortique.data.config_manager import ConfigManager
from sortique.data.database import Database
from sortique.data.lock_manager import LockManager
from sortique.data.models import DuplicateGroup, FileRecord, Session
from sortique.service.session_manager import (
    InvalidTransitionError,
    SessionManager,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path):
    """Fresh SQLite database."""
    database = Database(str(tmp_path / "test.db"))
    yield database
    database.close()


@pytest.fixture()
def config(tmp_path):
    """ConfigManager using a temp directory (no user overrides)."""
    return ConfigManager(config_dir=str(tmp_path / "config"))


@pytest.fixture()
def mgr(db, config):
    """Convenience SessionManager fixture."""
    return SessionManager(db, config)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_file_record(
    session_id: str,
    status: FileStatus = FileStatus.PENDING,
    **overrides,
) -> FileRecord:
    kwargs: dict = dict(
        session_id=session_id,
        source_path="/photos/img.jpg",
        source_dir="/photos",
        file_type=FileType.IMAGE,
        status=status,
    )
    kwargs.update(overrides)
    return FileRecord(**kwargs)


# ===================================================================
# 1. Session creation
# ===================================================================


class TestCreateSession:

    def test_creates_in_pending_state(self, mgr):
        session = mgr.create_session(["/src"], "/dst")
        assert session.state == SessionState.PENDING

    def test_stores_source_dirs(self, mgr):
        session = mgr.create_session(["/a", "/b"], "/dst")
        assert session.source_dirs == ["/a", "/b"]

    def test_stores_destination_dir(self, mgr):
        session = mgr.create_session(["/src"], "/my/dest")
        assert session.destination_dir == "/my/dest"

    def test_config_snapshot_captured(self, mgr):
        session = mgr.create_session(["/src"], "/dst")
        # The snapshot should contain at least the built-in defaults.
        assert "jpeg_quality" in session.config_snapshot
        assert "threads" in session.config_snapshot

    def test_persisted_to_database(self, mgr, db):
        session = mgr.create_session(["/src"], "/dst")
        loaded = db.get_session(session.id)
        assert loaded is not None
        assert loaded.id == session.id
        assert loaded.state == SessionState.PENDING

    def test_default_stats_populated(self, mgr):
        session = mgr.create_session(["/src"], "/dst")
        assert session.stats["files_processed"] == 0
        assert session.stats["files_skipped"] == 0


# ===================================================================
# 2. Valid state transitions
# ===================================================================


class TestValidTransitions:

    def test_pending_to_in_progress(self, mgr):
        session = mgr.create_session(["/src"], "/dst")
        updated = mgr.transition(session.id, SessionState.IN_PROGRESS)
        assert updated.state == SessionState.IN_PROGRESS

    def test_in_progress_to_running(self, mgr):
        session = mgr.create_session(["/src"], "/dst")
        mgr.transition(session.id, SessionState.IN_PROGRESS)
        updated = mgr.transition(session.id, SessionState.RUNNING)
        assert updated.state == SessionState.RUNNING

    def test_running_to_paused(self, mgr):
        session = mgr.create_session(["/src"], "/dst")
        mgr.transition(session.id, SessionState.IN_PROGRESS)
        mgr.transition(session.id, SessionState.RUNNING)
        updated = mgr.transition(session.id, SessionState.PAUSED)
        assert updated.state == SessionState.PAUSED

    def test_paused_to_running(self, mgr):
        session = mgr.create_session(["/src"], "/dst")
        mgr.transition(session.id, SessionState.IN_PROGRESS)
        mgr.transition(session.id, SessionState.RUNNING)
        mgr.transition(session.id, SessionState.PAUSED)
        updated = mgr.transition(session.id, SessionState.RUNNING)
        assert updated.state == SessionState.RUNNING

    def test_running_to_completed(self, mgr):
        session = mgr.create_session(["/src"], "/dst")
        mgr.transition(session.id, SessionState.IN_PROGRESS)
        mgr.transition(session.id, SessionState.RUNNING)
        updated = mgr.transition(session.id, SessionState.COMPLETED)
        assert updated.state == SessionState.COMPLETED

    def test_running_to_error(self, mgr):
        session = mgr.create_session(["/src"], "/dst")
        mgr.transition(session.id, SessionState.IN_PROGRESS)
        mgr.transition(session.id, SessionState.RUNNING)
        updated = mgr.transition(session.id, SessionState.ERROR)
        assert updated.state == SessionState.ERROR

    def test_error_to_running_retry(self, mgr):
        session = mgr.create_session(["/src"], "/dst")
        mgr.transition(session.id, SessionState.IN_PROGRESS)
        mgr.transition(session.id, SessionState.RUNNING)
        mgr.transition(session.id, SessionState.ERROR)
        updated = mgr.transition(session.id, SessionState.RUNNING)
        assert updated.state == SessionState.RUNNING

    def test_stopped_to_running_resume(self, mgr):
        session = mgr.create_session(["/src"], "/dst")
        mgr.transition(session.id, SessionState.IN_PROGRESS)
        mgr.transition(session.id, SessionState.STOPPED)
        updated = mgr.transition(session.id, SessionState.RUNNING)
        assert updated.state == SessionState.RUNNING

    def test_completed_to_undone(self, mgr):
        session = mgr.create_session(["/src"], "/dst")
        mgr.transition(session.id, SessionState.IN_PROGRESS)
        mgr.transition(session.id, SessionState.RUNNING)
        mgr.transition(session.id, SessionState.COMPLETED)
        updated = mgr.transition(session.id, SessionState.UNDONE)
        assert updated.state == SessionState.UNDONE

    def test_transition_updates_timestamp(self, mgr):
        session = mgr.create_session(["/src"], "/dst")
        original_ts = session.updated_at
        updated = mgr.transition(session.id, SessionState.IN_PROGRESS)
        assert updated.updated_at >= original_ts

    def test_transition_persisted(self, mgr, db):
        session = mgr.create_session(["/src"], "/dst")
        mgr.transition(session.id, SessionState.IN_PROGRESS)
        loaded = db.get_session(session.id)
        assert loaded.state == SessionState.IN_PROGRESS


# ===================================================================
# 3. Invalid transitions
# ===================================================================


class TestInvalidTransitions:

    def test_pending_to_running_raises(self, mgr):
        session = mgr.create_session(["/src"], "/dst")
        with pytest.raises(InvalidTransitionError):
            mgr.transition(session.id, SessionState.RUNNING)

    def test_pending_to_completed_raises(self, mgr):
        session = mgr.create_session(["/src"], "/dst")
        with pytest.raises(InvalidTransitionError):
            mgr.transition(session.id, SessionState.COMPLETED)

    def test_completed_to_running_raises(self, mgr):
        session = mgr.create_session(["/src"], "/dst")
        mgr.transition(session.id, SessionState.IN_PROGRESS)
        mgr.transition(session.id, SessionState.RUNNING)
        mgr.transition(session.id, SessionState.COMPLETED)
        with pytest.raises(InvalidTransitionError):
            mgr.transition(session.id, SessionState.RUNNING)

    def test_undone_is_terminal(self, mgr):
        session = mgr.create_session(["/src"], "/dst")
        mgr.transition(session.id, SessionState.IN_PROGRESS)
        mgr.transition(session.id, SessionState.RUNNING)
        mgr.transition(session.id, SessionState.COMPLETED)
        mgr.transition(session.id, SessionState.UNDONE)
        # Every possible target should be rejected.
        for state in SessionState:
            with pytest.raises(InvalidTransitionError):
                mgr.transition(session.id, state)

    def test_error_message_contains_states(self, mgr):
        session = mgr.create_session(["/src"], "/dst")
        with pytest.raises(InvalidTransitionError, match="pending.*completed"):
            mgr.transition(session.id, SessionState.COMPLETED)

    def test_missing_session_raises_value_error(self, mgr):
        with pytest.raises(ValueError, match="Session not found"):
            mgr.transition("nonexistent-id", SessionState.RUNNING)


# ===================================================================
# 4. Resumable session detection
# ===================================================================


class TestResumableSession:

    def test_finds_stopped_session(self, mgr):
        session = mgr.create_session(["/src"], "/dst/photos")
        mgr.transition(session.id, SessionState.IN_PROGRESS)
        mgr.transition(session.id, SessionState.STOPPED)

        found = mgr.get_resumable_session("/dst/photos")
        assert found is not None
        assert found.id == session.id

    def test_finds_error_session(self, mgr):
        session = mgr.create_session(["/src"], "/dst/photos")
        mgr.transition(session.id, SessionState.IN_PROGRESS)
        mgr.transition(session.id, SessionState.RUNNING)
        mgr.transition(session.id, SessionState.ERROR)

        found = mgr.get_resumable_session("/dst/photos")
        assert found is not None
        assert found.id == session.id

    def test_ignores_completed_session(self, mgr):
        session = mgr.create_session(["/src"], "/dst/photos")
        mgr.transition(session.id, SessionState.IN_PROGRESS)
        mgr.transition(session.id, SessionState.RUNNING)
        mgr.transition(session.id, SessionState.COMPLETED)

        found = mgr.get_resumable_session("/dst/photos")
        assert found is None

    def test_ignores_different_destination(self, mgr):
        session = mgr.create_session(["/src"], "/dst/a")
        mgr.transition(session.id, SessionState.IN_PROGRESS)
        mgr.transition(session.id, SessionState.STOPPED)

        found = mgr.get_resumable_session("/dst/b")
        assert found is None

    def test_returns_none_when_empty(self, mgr):
        found = mgr.get_resumable_session("/dst/photos")
        assert found is None

    def test_returns_most_recent(self, mgr):
        """list_sessions returns newest first; we expect the first match."""
        old = mgr.create_session(["/src"], "/dst/photos")
        mgr.transition(old.id, SessionState.STOPPED)

        new = mgr.create_session(["/src"], "/dst/photos")
        mgr.transition(new.id, SessionState.STOPPED)

        found = mgr.get_resumable_session("/dst/photos")
        assert found is not None
        assert found.id == new.id


# ===================================================================
# 5. Session stats
# ===================================================================


class TestGetSessionStats:

    def test_empty_session_all_zeros(self, mgr):
        session = mgr.create_session(["/src"], "/dst")
        stats = mgr.get_session_stats(session.id)
        assert stats == {
            "files_processed": 0,
            "files_skipped": 0,
            "files_errored": 0,
            "dupes_found": 0,
            "space_saved": 0,
        }

    def test_counts_by_status(self, mgr, db):
        session = mgr.create_session(["/src"], "/dst")

        for status, count in [
            (FileStatus.COMPLETED, 5),
            (FileStatus.SKIPPED, 3),
            (FileStatus.ERROR, 2),
            (FileStatus.PENDING, 4),
        ]:
            for i in range(count):
                rec = _make_file_record(
                    session.id,
                    status=status,
                    source_path=f"/photos/{status.value}_{i}.jpg",
                )
                db.create_file_record(rec)

        stats = mgr.get_session_stats(session.id)
        assert stats["files_processed"] == 5
        assert stats["files_skipped"] == 3
        assert stats["files_errored"] == 2

    def test_duplicate_stats(self, mgr, db):
        session = mgr.create_session(["/src"], "/dst")

        g1 = DuplicateGroup(
            session_id=session.id,
            winner_file_id="w1",
            hash_value="abc",
            match_type=DupMatchType.EXACT,
            file_count=3,
            bytes_saved=5000,
        )
        g2 = DuplicateGroup(
            session_id=session.id,
            winner_file_id="w2",
            hash_value="def",
            match_type=DupMatchType.EXACT,
            file_count=2,
            bytes_saved=3000,
        )
        db.create_duplicate_group(g1)
        db.create_duplicate_group(g2)

        stats = mgr.get_session_stats(session.id)
        assert stats["dupes_found"] == 2
        assert stats["space_saved"] == 8000


# ===================================================================
# 6. Finalize session
# ===================================================================


class TestFinalizeSession:

    def _run_to_running(self, mgr, session):
        """Helper: advance a session to RUNNING."""
        mgr.transition(session.id, SessionState.IN_PROGRESS)
        mgr.transition(session.id, SessionState.RUNNING)

    def test_marks_completed(self, mgr, db):
        session = mgr.create_session(["/src"], "/dst")
        self._run_to_running(mgr, session)

        mgr.finalize_session(session.id)

        loaded = db.get_session(session.id)
        assert loaded.state == SessionState.COMPLETED

    def test_computes_final_stats(self, mgr, db):
        session = mgr.create_session(["/src"], "/dst")
        self._run_to_running(mgr, session)

        for i in range(3):
            rec = _make_file_record(
                session.id,
                status=FileStatus.COMPLETED,
                source_path=f"/photos/img_{i}.jpg",
            )
            db.create_file_record(rec)

        mgr.finalize_session(session.id)

        loaded = db.get_session(session.id)
        assert loaded.stats["files_processed"] == 3

    def test_releases_lock(self, mgr, tmp_path):
        dest = str(tmp_path / "dest")
        os.makedirs(dest, exist_ok=True)

        session = mgr.create_session(["/src"], dest)
        self._run_to_running(mgr, session)

        # Simulate an active lock owned by this process.
        lock = LockManager(dest)
        assert lock.acquire() is True
        assert lock.is_locked()

        mgr.finalize_session(session.id)

        assert not lock.is_locked()

    def test_finalize_from_invalid_state_raises(self, mgr):
        session = mgr.create_session(["/src"], "/dst")
        # Session is in PENDING — cannot finalize.
        with pytest.raises(InvalidTransitionError):
            mgr.finalize_session(session.id)

    def test_finalize_missing_session_raises(self, mgr):
        with pytest.raises(ValueError, match="Session not found"):
            mgr.finalize_session("nonexistent-id")
