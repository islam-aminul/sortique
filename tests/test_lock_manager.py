"""Tests for sortique.data.lock_manager.LockManager."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from sortique.data.lock_manager import LockManager


@pytest.fixture()
def dest_dir(tmp_path: Path) -> str:
    d = tmp_path / "dest"
    d.mkdir()
    return str(d)


# ======================================================================
# Acquire / release cycle
# ======================================================================

class TestAcquireRelease:
    def test_acquire_creates_lock_file(self, dest_dir: str) -> None:
        lm = LockManager(dest_dir)
        assert lm.acquire() is True
        assert lm.is_locked()

        info = lm.get_lock_info()
        assert info is not None
        assert info["pid"] == os.getpid()
        assert "started_at" in info
        assert "hostname" in info

    def test_release_removes_lock_file(self, dest_dir: str) -> None:
        lm = LockManager(dest_dir)
        lm.acquire()
        lm.release()
        assert not lm.is_locked()

    def test_release_is_idempotent(self, dest_dir: str) -> None:
        lm = LockManager(dest_dir)
        lm.acquire()
        lm.release()
        lm.release()  # second release should not raise
        assert not lm.is_locked()

    def test_acquire_twice_by_same_instance_returns_true(self, dest_dir: str) -> None:
        lm = LockManager(dest_dir)
        assert lm.acquire() is True
        assert lm.acquire() is True  # already own it
        lm.release()

    def test_release_only_removes_own_lock(self, dest_dir: str) -> None:
        """If another PID wrote the lock, release() must not remove it."""
        lm = LockManager(dest_dir)
        # Manually write a lock belonging to a different PID.
        _write_fake_lock(dest_dir, pid=os.getpid() + 99999)
        # lm never called acquire(), so _locked is False.
        lm.release()
        assert lm.is_locked()  # file still present


# ======================================================================
# Double-acquire from separate instances (simulated concurrent session)
# ======================================================================

class TestDoubleAcquire:
    def test_second_instance_fails_when_pid_is_running(self, dest_dir: str) -> None:
        lm1 = LockManager(dest_dir)
        assert lm1.acquire() is True

        lm2 = LockManager(dest_dir)
        # Our own PID is running, so lm2 must fail.
        assert lm2.acquire() is False
        assert lm2.is_stale is False

        lm1.release()

    def test_second_instance_succeeds_after_first_releases(self, dest_dir: str) -> None:
        lm1 = LockManager(dest_dir)
        lm1.acquire()
        lm1.release()

        lm2 = LockManager(dest_dir)
        assert lm2.acquire() is True
        lm2.release()


# ======================================================================
# Stale lock detection
# ======================================================================

class TestStaleLock:
    def test_stale_lock_detected(self, dest_dir: str) -> None:
        """A lock file whose PID is not running should be flagged stale."""
        _write_fake_lock(dest_dir, pid=_dead_pid())

        lm = LockManager(dest_dir)
        assert lm.acquire() is False
        assert lm.is_stale is True

    def test_stale_lock_get_info(self, dest_dir: str) -> None:
        fake_pid = _dead_pid()
        _write_fake_lock(dest_dir, pid=fake_pid)

        lm = LockManager(dest_dir)
        info = lm.get_lock_info()
        assert info is not None
        assert info["pid"] == fake_pid

    def test_is_stale_false_when_no_lock(self, dest_dir: str) -> None:
        lm = LockManager(dest_dir)
        assert lm.is_stale is False


# ======================================================================
# Force-acquire
# ======================================================================

class TestForceAcquire:
    def test_force_acquire_replaces_stale_lock(self, dest_dir: str) -> None:
        _write_fake_lock(dest_dir, pid=_dead_pid())

        lm = LockManager(dest_dir)
        assert lm.acquire() is False
        assert lm.is_stale is True

        assert lm.force_acquire() is True
        assert lm.is_stale is False

        info = lm.get_lock_info()
        assert info["pid"] == os.getpid()

        lm.release()
        assert not lm.is_locked()

    def test_force_acquire_when_no_lock_exists(self, dest_dir: str) -> None:
        lm = LockManager(dest_dir)
        assert lm.force_acquire() is True
        lm.release()

    def test_force_acquire_creates_destination_dir(self, tmp_path: Path) -> None:
        new_dest = str(tmp_path / "new" / "dest")
        lm = LockManager(new_dest)
        assert lm.force_acquire() is True
        assert os.path.isdir(new_dest)
        lm.release()


# ======================================================================
# Context manager
# ======================================================================

class TestContextManager:
    def test_context_manager_acquires_and_releases(self, dest_dir: str) -> None:
        lm = LockManager(dest_dir)
        with lm:
            assert lm.is_locked()
            assert lm.get_lock_info()["pid"] == os.getpid()
        assert not lm.is_locked()

    def test_context_manager_raises_on_failure(self, dest_dir: str) -> None:
        # Pre-lock with our own running PID via a separate instance.
        lm1 = LockManager(dest_dir)
        lm1.acquire()

        lm2 = LockManager(dest_dir)
        with pytest.raises(RuntimeError, match="Could not acquire lock"):
            with lm2:
                pass  # pragma: no cover

        lm1.release()

    def test_context_manager_releases_on_exception(self, dest_dir: str) -> None:
        lm = LockManager(dest_dir)
        with pytest.raises(ValueError):
            with lm:
                assert lm.is_locked()
                raise ValueError("boom")
        assert not lm.is_locked()


# ======================================================================
# Edge cases
# ======================================================================

class TestEdgeCases:
    def test_get_lock_info_returns_none_when_absent(self, dest_dir: str) -> None:
        lm = LockManager(dest_dir)
        assert lm.get_lock_info() is None

    def test_corrupt_lock_file_treated_as_absent(self, dest_dir: str) -> None:
        lock_path = os.path.join(dest_dir, LockManager.LOCK_FILENAME)
        with open(lock_path, "w") as f:
            f.write("NOT VALID JSON{{{")

        lm = LockManager(dest_dir)
        assert lm.get_lock_info() is None
        # acquire() should succeed because get_lock_info returns None.
        assert lm.acquire() is True
        lm.release()

    def test_is_pid_running_zero_or_negative(self) -> None:
        assert LockManager._is_pid_running(0) is False
        assert LockManager._is_pid_running(-1) is False

    def test_is_pid_running_current_process(self) -> None:
        assert LockManager._is_pid_running(os.getpid()) is True


# ======================================================================
# Helpers
# ======================================================================

def _write_fake_lock(dest_dir: str, pid: int) -> None:
    """Write a lock file with an arbitrary PID."""
    lock_path = os.path.join(dest_dir, LockManager.LOCK_FILENAME)
    with open(lock_path, "w", encoding="utf-8") as f:
        json.dump(
            {"pid": pid, "started_at": "2025-01-01T00:00:00+00:00", "hostname": "test"},
            f,
        )


def _dead_pid() -> int:
    """Return a PID that is (almost certainly) not running.

    We pick a very large number.  On Windows the max PID is ~4 million;
    on Linux it defaults to 32768 (or 4194304 with pid_max raised).
    """
    candidate = 4_000_111
    # Make sure it really isn't alive (astronomically unlikely, but be safe).
    if LockManager._is_pid_running(candidate):
        candidate += 1
    return candidate
