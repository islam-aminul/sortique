"""Cross-platform file system operations with safety guarantees."""

from __future__ import annotations

import ctypes
import os
import platform
import re
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from sortique.constants import (
    CLOUD_STUB_PATTERNS,
    HIDDEN_SYSTEM_FILES,
    LARGE_FILE_THRESHOLD,
    PROGRESS_INTERVAL,
    SKIP_DIRS,
)

_SYSTEM = platform.system()  # "Windows", "Darwin", "Linux"


class FileSystemHelper:
    """Cross-platform file system operations with safety guarantees."""

    # ------------------------------------------------------------------
    # Atomic copy
    # ------------------------------------------------------------------

    @staticmethod
    def atomic_copy(
        src: str,
        dst: str,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> bool:
        """Copy *src* to *dst* atomically.

        Writes to a ``.tmp`` sibling in the destination directory, then renames.
        For files larger than ``LARGE_FILE_THRESHOLD`` (100 MB) the optional
        *progress_callback(bytes_copied, total_bytes)* is invoked periodically.

        Uses :func:`shutil.copy2` semantics to preserve metadata.
        Returns ``True`` on success.
        """
        dst_path = Path(dst)
        dst_path.parent.mkdir(parents=True, exist_ok=True)

        total = os.path.getsize(src)
        fd, tmp_path = tempfile.mkstemp(
            suffix=".tmp", dir=str(dst_path.parent),
        )
        os.close(fd)

        try:
            if total > LARGE_FILE_THRESHOLD and progress_callback is not None:
                _chunked_copy(src, tmp_path, total, progress_callback)
            else:
                shutil.copy2(src, tmp_path)

            # Preserve metadata explicitly after a chunked copy as well.
            shutil.copystat(src, tmp_path)

            # Atomic rename (same filesystem guaranteed: same parent dir).
            os.replace(tmp_path, dst)
            return True
        except BaseException:
            # Clean up the temp artefact on any failure.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------
    # Free space
    # ------------------------------------------------------------------

    @staticmethod
    def get_free_space(path: str) -> int:
        """Return free bytes on the filesystem containing *path*."""
        return shutil.disk_usage(path).free

    # ------------------------------------------------------------------
    # Hidden / system / skip helpers
    # ------------------------------------------------------------------

    @staticmethod
    def is_hidden_or_system(filepath: str) -> bool:
        """``True`` when the filename starts with ``.`` or is a known system file."""
        # Normalise path separators so Windows-style paths work on all platforms.
        name = os.path.basename(filepath.replace("\\", "/"))
        return name.startswith(".") or name in HIDDEN_SYSTEM_FILES

    @staticmethod
    def is_skip_directory(dirname: str) -> bool:
        """``True`` when *dirname* (basename only) is in ``SKIP_DIRS``."""
        return os.path.basename(dirname) in SKIP_DIRS

    # ------------------------------------------------------------------
    # Symlinks
    # ------------------------------------------------------------------

    @staticmethod
    def is_symlink(path: str) -> bool:
        return os.path.islink(path)

    @staticmethod
    def resolve_symlink(path: str) -> str | None:
        """Resolve a symlink to its real path.  Returns ``None`` for broken links."""
        try:
            resolved = os.path.realpath(path)
            if os.path.exists(resolved):
                return resolved
            return None
        except OSError:
            return None

    # ------------------------------------------------------------------
    # Cloud stub detection
    # ------------------------------------------------------------------

    @staticmethod
    def is_cloud_stub(filepath: str) -> tuple[bool, str]:
        """Detect cloud-sync placeholder / stub files.

        Returns ``(is_stub, cloud_service_name)``.
        """
        name = os.path.basename(filepath)
        _, ext = os.path.splitext(name)
        ext_lower = ext.lower()

        # --- iCloud ---
        for pattern in CLOUD_STUB_PATTERNS["icloud"]:
            if name.endswith(pattern):
                return True, "icloud"
        # macOS iCloud container path
        if _SYSTEM == "Darwin":
            try:
                real = os.path.realpath(filepath)
                if "/Library/Mobile Documents/" in real:
                    return True, "icloud"
            except OSError:
                pass

        # --- OneDrive (Windows dehydrated files) ---
        if _SYSTEM == "Windows":
            try:
                FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000
                attrs = ctypes.windll.kernel32.GetFileAttributesW(filepath)
                if attrs != -1 and attrs & FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS:
                    return True, "onedrive"
            except (AttributeError, OSError):
                pass
        for pattern in CLOUD_STUB_PATTERNS["onedrive"]:
            if name.endswith(pattern):
                return True, "onedrive"

        # --- Dropbox (xattr on macOS/Linux) ---
        if _SYSTEM in ("Darwin", "Linux"):
            try:
                xattrs = os.listxattr(filepath)
                if "com.dropbox.attrs" in xattrs:
                    return True, "dropbox"
            except (OSError, AttributeError):
                pass
        for pattern in CLOUD_STUB_PATTERNS["dropbox"]:
            if name.endswith(pattern):
                return True, "dropbox"

        # --- Google Drive (zero-byte stubs) ---
        for pattern in CLOUD_STUB_PATTERNS["gdrive"]:
            if ext_lower == pattern:
                try:
                    if os.path.getsize(filepath) == 0:
                        return True, "gdrive"
                except OSError:
                    pass

        return False, ""

    # ------------------------------------------------------------------
    # Filename sanitization
    # ------------------------------------------------------------------

    @staticmethod
    def sanitize_filename(filename: str, target_os: str | None = None) -> str:
        """Produce a filesystem-safe filename for *target_os*.

        * ``'windows'`` — strip ``\\ / : * ? " < > |``
        * ``'linux'``   — strip ``/`` and null byte
        * ``'macos'``   — strip ``:`` and ``/``
        * ``None``      — auto-detect the current platform

        All valid Unicode (accented characters, CJK, emoji) is preserved.
        Leading/trailing whitespace and dots are trimmed.  Returns
        ``'unnamed'`` if the result is empty.
        """
        if target_os is None:
            if _SYSTEM == "Windows":
                target_os = "windows"
            elif _SYSTEM == "Darwin":
                target_os = "macos"
            else:
                target_os = "linux"

        if target_os == "windows":
            # Characters illegal on NTFS / Windows API.
            result = re.sub(r'[\\/:*?"<>|]', "_", filename)
        elif target_os == "macos":
            result = re.sub(r'[:/]', "_", filename)
        else:  # linux
            result = filename.replace("/", "_").replace("\x00", "_")

        result = result.strip().strip(".")

        return result if result else "unnamed"

    # ------------------------------------------------------------------
    # Stat helpers
    # ------------------------------------------------------------------

    @staticmethod
    def get_file_mtime(filepath: str) -> float:
        return os.path.getmtime(filepath)

    @staticmethod
    def get_file_size(filepath: str) -> int:
        return os.path.getsize(filepath)

    @staticmethod
    def files_match(path: str, expected_size: int, expected_mtime: float) -> bool:
        """``True`` when the file at *path* still has the recorded size and mtime."""
        try:
            st = os.stat(path)
            return st.st_size == expected_size and st.st_mtime == expected_mtime
        except OSError:
            return False

    # ------------------------------------------------------------------
    # Directory helpers
    # ------------------------------------------------------------------

    @staticmethod
    def ensure_directory(path: str) -> None:
        os.makedirs(path, exist_ok=True)

    @staticmethod
    def remove_empty_parents(path: str, stop_at: str) -> None:
        """Remove empty parent directories of *path* up to (but not including) *stop_at*.

        Used during undo to clean up directory trees that were created during
        the organise step.
        """
        stop = os.path.normpath(os.path.abspath(stop_at))
        current = os.path.normpath(os.path.abspath(path))

        while True:
            parent = os.path.dirname(current)
            parent = os.path.normpath(parent)

            # Reached or passed the stop boundary.
            if parent == stop or parent == current:
                break

            # Also stop if current directory is *above* stop_at (safety).
            if not parent.startswith(stop):
                break

            try:
                os.rmdir(parent)  # only succeeds when empty
            except OSError:
                break

            current = parent


# ------------------------------------------------------------------
# Module-private helpers
# ------------------------------------------------------------------

def _chunked_copy(
    src: str,
    dst: str,
    total: int,
    callback: Callable[[int, int], None],
    chunk_size: int = 1024 * 1024,  # 1 MB
) -> None:
    """Copy *src* → *dst* in chunks, invoking *callback* at ``PROGRESS_INTERVAL``."""
    copied = 0
    last_report = 0.0
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        while True:
            buf = fin.read(chunk_size)
            if not buf:
                break
            fout.write(buf)
            copied += len(buf)

            now = time.monotonic()
            if now - last_report >= PROGRESS_INTERVAL:
                callback(copied, total)
                last_report = now

    # Final callback so the caller always sees 100 %.
    callback(total, total)
