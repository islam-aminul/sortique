"""Session lock manager — prevents concurrent access to a destination directory."""

from __future__ import annotations

import json
import os
import platform
import socket
import signal
from datetime import datetime, timezone


class LockManager:
    """Manages a ``.sortique.lock`` file inside the destination directory.

    Only one Sortique session should write to a given destination at a time.
    The lock file is a small JSON document recording the owning PID, hostname,
    and start timestamp so that stale locks can be detected and reported.
    """

    LOCK_FILENAME = ".sortique.lock"

    def __init__(self, destination_dir: str) -> None:
        self.destination_dir = destination_dir
        self.lock_path = os.path.join(destination_dir, self.LOCK_FILENAME)
        self._locked = False
        self._stale = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def acquire(self) -> bool:
        """Try to take the lock.

        * No lock file → create one with our PID, return ``True``.
        * Lock file with a **running** PID → return ``False``.
        * Lock file with a **dead** PID → set ``is_stale``, return ``False``.
        """
        if self._locked:
            return True

        info = self.get_lock_info()
        if info is not None:
            pid = info.get("pid", -1)
            if self._is_pid_running(pid):
                self._stale = False
                return False
            # PID is gone — stale lock.
            self._stale = True
            return False

        self._write_lock()
        return True

    def force_acquire(self) -> bool:
        """Remove an existing (stale) lock and acquire a fresh one."""
        try:
            os.unlink(self.lock_path)
        except FileNotFoundError:
            pass
        self._stale = False
        self._write_lock()
        return True

    def release(self) -> None:
        """Remove the lock file **only** if we own it (matching PID)."""
        if not self._locked:
            return
        info = self.get_lock_info()
        if info is not None and info.get("pid") == os.getpid():
            try:
                os.unlink(self.lock_path)
            except FileNotFoundError:
                pass
        self._locked = False
        self._stale = False

    def is_locked(self) -> bool:
        """``True`` when a lock file exists on disk, regardless of owner."""
        return os.path.exists(self.lock_path)

    @property
    def is_stale(self) -> bool:
        """``True`` after :meth:`acquire` found a lock with a non-running PID."""
        return self._stale

    def get_lock_info(self) -> dict | None:
        """Read and return the lock-file contents, or ``None`` if absent/corrupt."""
        try:
            with open(self.lock_path, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None

    # ------------------------------------------------------------------
    # Context-manager protocol
    # ------------------------------------------------------------------

    def __enter__(self) -> LockManager:
        if not self.acquire():
            raise RuntimeError(f"Could not acquire lock on {self.destination_dir}")
        return self

    def __exit__(self, *args: object) -> None:
        self.release()

    # ------------------------------------------------------------------
    # PID helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_pid_running(pid: int) -> bool:
        """Cross-platform check whether *pid* refers to a live process."""
        if pid <= 0:
            return False

        if platform.system() == "Windows":
            return _is_pid_running_windows(pid)

        # Unix: sending signal 0 checks existence without actually signalling.
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # Process exists but we lack permission to signal it.
            return True

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _write_lock(self) -> None:
        os.makedirs(self.destination_dir, exist_ok=True)
        payload = {
            "pid": os.getpid(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "hostname": socket.gethostname(),
        }
        with open(self.lock_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        self._locked = True


# ------------------------------------------------------------------
# Windows PID helper (module-level to keep the class body readable)
# ------------------------------------------------------------------

def _is_pid_running_windows(pid: int) -> bool:
    """Check if *pid* is alive on Windows via ``OpenProcess``."""
    import ctypes
    import ctypes.wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.wintypes.DWORD()
        if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return exit_code.value == STILL_ACTIVE
        return False
    finally:
        kernel32.CloseHandle(handle)
