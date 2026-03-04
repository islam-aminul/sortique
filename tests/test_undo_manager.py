"""Tests for UndoManager, UndoVerification, and UndoResult."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from sortique.constants import FileStatus, SessionState
from sortique.data.models import FileRecord, Session
from sortique.service.undo_manager import (
    UndoManager,
    UndoResult,
    UndoVerification,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_record(
    source_path: str = "/src/photo.jpg",
    destination_path: str | None = "/dst/Photos/photo.jpg",
    *,
    session_id: str = "sess-1",
    file_size: int = 1000,
    status: FileStatus = FileStatus.COMPLETED,
) -> FileRecord:
    return FileRecord(
        session_id=session_id,
        source_path=source_path,
        source_dir=os.path.dirname(source_path),
        destination_path=destination_path,
        file_size=file_size,
        status=status,
    )


def _make_session(
    session_id: str = "sess-1",
    state: SessionState = SessionState.COMPLETED,
    destination_dir: str = "/dst",
) -> Session:
    return Session(
        id=session_id,
        state=state,
        destination_dir=destination_dir,
    )


def _build_manager(
    *,
    session: Session | None = None,
    records: list[FileRecord] | None = None,
) -> UndoManager:
    """Create an UndoManager with mocked DB and SessionManager."""
    db = MagicMock()
    db.get_session.return_value = session or _make_session()
    db.get_file_records.return_value = records or []

    sm = MagicMock()
    sm.transition.return_value = session or _make_session(state=SessionState.UNDONE)

    return UndoManager(db, sm)


# ---------------------------------------------------------------------------
# Dataclass defaults
# ---------------------------------------------------------------------------

class TestDataclasses:
    def test_undo_verification_defaults(self):
        v = UndoVerification()
        assert v.total_files == 0
        assert v.files_present == 0
        assert v.files_missing == 0
        assert v.bytes_to_free == 0
        assert v.safe_to_proceed is True

    def test_undo_result_defaults(self):
        r = UndoResult()
        assert r.success is False
        assert r.files_deleted == 0
        assert r.files_missing == 0
        assert r.folders_removed == 0
        assert r.errors == []


# ---------------------------------------------------------------------------
# Verify finds all files
# ---------------------------------------------------------------------------

class TestVerifyFindsAllFiles:
    def test_all_files_present(self, tmp_path):
        dest_a = tmp_path / "Photos" / "a.jpg"
        dest_b = tmp_path / "Photos" / "b.jpg"
        dest_a.parent.mkdir(parents=True)
        dest_a.write_bytes(b"x" * 500)
        dest_b.write_bytes(b"y" * 300)

        records = [
            _make_record(destination_path=str(dest_a), file_size=500),
            _make_record(destination_path=str(dest_b), file_size=300),
        ]
        mgr = _build_manager(records=records)

        v = mgr.verify("sess-1")

        assert v.total_files == 2
        assert v.files_present == 2
        assert v.files_missing == 0
        assert v.bytes_to_free == 800
        assert v.safe_to_proceed is True

    def test_skips_records_without_destination(self):
        records = [
            _make_record(destination_path=None),
            _make_record(destination_path=None),
        ]
        mgr = _build_manager(records=records)

        v = mgr.verify("sess-1")

        assert v.total_files == 0
        assert v.safe_to_proceed is True

    def test_empty_session(self):
        mgr = _build_manager(records=[])

        v = mgr.verify("sess-1")

        assert v.total_files == 0
        assert v.files_present == 0
        assert v.safe_to_proceed is True


# ---------------------------------------------------------------------------
# Verify detects missing files
# ---------------------------------------------------------------------------

class TestVerifyDetectsMissingFiles:
    def test_some_files_missing(self, tmp_path):
        dest_a = tmp_path / "Photos" / "a.jpg"
        dest_a.parent.mkdir(parents=True)
        dest_a.write_bytes(b"x" * 500)

        records = [
            _make_record(destination_path=str(dest_a), file_size=500),
            _make_record(destination_path=str(tmp_path / "Photos" / "gone.jpg")),
        ]
        mgr = _build_manager(records=records)

        v = mgr.verify("sess-1")

        assert v.total_files == 2
        assert v.files_present == 1
        assert v.files_missing == 1
        assert v.bytes_to_free == 500

    def test_all_files_missing(self):
        records = [
            _make_record(destination_path="/nonexistent/a.jpg"),
            _make_record(destination_path="/nonexistent/b.jpg"),
        ]
        mgr = _build_manager(records=records)

        v = mgr.verify("sess-1")

        assert v.total_files == 2
        assert v.files_present == 0
        assert v.files_missing == 2
        assert v.bytes_to_free == 0
        assert v.safe_to_proceed is False

    def test_safe_threshold_boundary(self, tmp_path):
        """Exactly 90% present → safe; below 90% → not safe."""
        files = []
        records = []
        for i in range(10):
            p = tmp_path / f"f{i}.jpg"
            if i < 9:  # 9 out of 10 present = 90%
                p.write_bytes(b"x")
                files.append(p)
            records.append(_make_record(destination_path=str(p)))

        mgr = _build_manager(records=records)

        v = mgr.verify("sess-1")

        assert v.files_present == 9
        assert v.files_missing == 1
        assert v.safe_to_proceed is True  # 9/10 = 90% >= threshold

    def test_below_safety_threshold(self, tmp_path):
        """Below 90% present → not safe."""
        records = []
        for i in range(10):
            p = tmp_path / f"f{i}.jpg"
            if i < 8:  # 8 out of 10 present = 80%
                p.write_bytes(b"x")
            records.append(_make_record(destination_path=str(p)))

        mgr = _build_manager(records=records)

        v = mgr.verify("sess-1")

        assert v.files_present == 8
        assert v.files_missing == 2
        assert v.safe_to_proceed is False  # 8/10 = 80% < threshold


# ---------------------------------------------------------------------------
# Execute deletes files and cleans empty folders
# ---------------------------------------------------------------------------

class TestExecuteDeletesAndCleans:
    def test_deletes_destination_files(self, tmp_path):
        dest_dir = tmp_path / "dst"
        photos = dest_dir / "Photos"
        photos.mkdir(parents=True)

        fa = photos / "a.jpg"
        fb = photos / "b.jpg"
        fa.write_bytes(b"a" * 100)
        fb.write_bytes(b"b" * 200)

        records = [
            _make_record(destination_path=str(fa)),
            _make_record(destination_path=str(fb)),
        ]
        session = _make_session(destination_dir=str(dest_dir))
        mgr = _build_manager(session=session, records=records)

        result = mgr.execute("sess-1", force=True)

        assert result.success is True
        assert result.files_deleted == 2
        assert not fa.exists()
        assert not fb.exists()

    def test_removes_empty_parent_directories(self, tmp_path):
        dest_dir = tmp_path / "dst"
        deep = dest_dir / "Photos" / "2024" / "January"
        deep.mkdir(parents=True)

        f = deep / "photo.jpg"
        f.write_bytes(b"x")

        records = [_make_record(destination_path=str(f))]
        session = _make_session(destination_dir=str(dest_dir))
        mgr = _build_manager(session=session, records=records)

        result = mgr.execute("sess-1", force=True)

        assert result.files_deleted == 1
        assert result.folders_removed >= 1
        # The empty directories should be removed.
        assert not deep.exists()

    def test_preserves_non_empty_directories(self, tmp_path):
        dest_dir = tmp_path / "dst"
        photos = dest_dir / "Photos"
        photos.mkdir(parents=True)

        fa = photos / "a.jpg"
        fb = photos / "keep.txt"
        fa.write_bytes(b"a")
        fb.write_bytes(b"keep me")

        records = [_make_record(destination_path=str(fa))]
        session = _make_session(destination_dir=str(dest_dir))
        mgr = _build_manager(session=session, records=records)

        result = mgr.execute("sess-1", force=True)

        assert result.files_deleted == 1
        assert not fa.exists()
        # Photos dir still has keep.txt, should not be removed.
        assert photos.exists()
        assert fb.exists()

    def test_handles_already_missing_files(self, tmp_path):
        dest_dir = tmp_path / "dst"
        dest_dir.mkdir()

        records = [
            _make_record(destination_path=str(dest_dir / "gone.jpg")),
        ]
        session = _make_session(destination_dir=str(dest_dir))
        mgr = _build_manager(session=session, records=records)

        result = mgr.execute("sess-1", force=True)

        assert result.files_missing == 1
        assert result.files_deleted == 0
        assert result.success is True

    def test_does_not_delete_destination_root(self, tmp_path):
        dest_dir = tmp_path / "dst"
        photos = dest_dir / "Photos"
        photos.mkdir(parents=True)

        f = photos / "a.jpg"
        f.write_bytes(b"x")

        records = [_make_record(destination_path=str(f))]
        session = _make_session(destination_dir=str(dest_dir))
        mgr = _build_manager(session=session, records=records)

        mgr.execute("sess-1", force=True)

        # Destination root should survive.
        assert dest_dir.exists()


# ---------------------------------------------------------------------------
# Execute transitions session to UNDONE
# ---------------------------------------------------------------------------

class TestExecuteTransitionsState:
    def test_transitions_to_undone(self, tmp_path):
        dest_dir = tmp_path / "dst"
        dest_dir.mkdir()

        session = _make_session(destination_dir=str(dest_dir))
        mgr = _build_manager(session=session, records=[])

        result = mgr.execute("sess-1", force=True)

        mgr.session_manager.transition.assert_called_once_with(
            "sess-1", SessionState.UNDONE,
        )
        assert result.success is True

    def test_transition_error_recorded(self, tmp_path):
        dest_dir = tmp_path / "dst"
        dest_dir.mkdir()

        session = _make_session(destination_dir=str(dest_dir))
        mgr = _build_manager(session=session, records=[])
        mgr.session_manager.transition.side_effect = Exception("bad state")

        result = mgr.execute("sess-1", force=True)

        assert result.success is False
        assert any("State transition failed" in e for e in result.errors)

    def test_session_not_found(self):
        mgr = _build_manager()
        mgr.db.get_session.return_value = None

        result = mgr.execute("nonexistent")

        assert result.success is False
        assert any("Session not found" in e for e in result.errors)

    def test_safety_check_aborts_when_not_safe(self, tmp_path):
        """Without force, undo aborts if too many files are missing."""
        records = [
            _make_record(destination_path="/nonexistent/a.jpg"),
            _make_record(destination_path="/nonexistent/b.jpg"),
        ]
        session = _make_session(destination_dir=str(tmp_path))
        mgr = _build_manager(session=session, records=records)

        result = mgr.execute("sess-1", force=False)

        assert result.success is False
        assert result.files_missing == 2
        assert any("force=True" in e for e in result.errors)
        # Transition should NOT have been called.
        mgr.session_manager.transition.assert_not_called()

    def test_force_bypasses_safety_check(self, tmp_path):
        """With force=True, undo proceeds even with many missing files."""
        records = [
            _make_record(destination_path="/nonexistent/a.jpg"),
            _make_record(destination_path="/nonexistent/b.jpg"),
        ]
        session = _make_session(destination_dir=str(tmp_path))
        mgr = _build_manager(session=session, records=records)

        result = mgr.execute("sess-1", force=True)

        # Still succeeds — missing files are not errors.
        assert result.success is True
        assert result.files_missing == 2
        mgr.session_manager.transition.assert_called_once()


# ---------------------------------------------------------------------------
# Partial failure handling
# ---------------------------------------------------------------------------

class TestPartialFailure:
    def test_permission_error_continues(self, tmp_path):
        dest_dir = tmp_path / "dst"
        photos = dest_dir / "Photos"
        photos.mkdir(parents=True)

        fa = photos / "a.jpg"
        fb = photos / "b.jpg"
        fa.write_bytes(b"a")
        fb.write_bytes(b"b")

        records = [
            _make_record(destination_path=str(fa)),
            _make_record(destination_path=str(fb)),
        ]
        session = _make_session(destination_dir=str(dest_dir))
        mgr = _build_manager(session=session, records=records)

        # Make the first delete fail, second succeed.
        delete_results = iter([False, True])

        with patch.object(
            UndoManager, "_delete_file_safe",
            staticmethod(lambda path: next(delete_results)),
        ):
            result = mgr.execute("sess-1", force=True)

        assert result.files_deleted == 1
        assert len(result.errors) >= 1
        assert any("Failed to delete" in e for e in result.errors)
        assert result.success is False

    def test_delete_file_safe_success(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_bytes(b"hello")

        assert UndoManager._delete_file_safe(str(f)) is True
        assert not f.exists()

    def test_delete_file_safe_nonexistent(self):
        assert UndoManager._delete_file_safe("/nonexistent/file.txt") is False

    def test_mixed_success_and_missing(self, tmp_path):
        dest_dir = tmp_path / "dst"
        photos = dest_dir / "Photos"
        photos.mkdir(parents=True)

        fa = photos / "a.jpg"
        fa.write_bytes(b"a" * 100)

        records = [
            _make_record(destination_path=str(fa)),
            _make_record(destination_path=str(photos / "gone.jpg")),
            _make_record(destination_path=str(photos / "also_gone.jpg")),
        ]
        session = _make_session(destination_dir=str(dest_dir))
        mgr = _build_manager(session=session, records=records)

        result = mgr.execute("sess-1", force=True)

        assert result.files_deleted == 1
        assert result.files_missing == 2
        assert result.success is True
        assert not fa.exists()

    def test_records_without_destination_skipped(self, tmp_path):
        dest_dir = tmp_path / "dst"
        dest_dir.mkdir()

        records = [
            _make_record(destination_path=None),
        ]
        session = _make_session(destination_dir=str(dest_dir))
        mgr = _build_manager(session=session, records=records)

        result = mgr.execute("sess-1", force=True)

        assert result.files_deleted == 0
        assert result.files_missing == 0
        assert result.success is True
