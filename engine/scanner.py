"""Recursive directory scanner with exclusion rules and incremental support."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass, field

from sortique.data.config_manager import ConfigManager
from sortique.data.file_system import FileSystemHelper
from sortique.data.models import SourceManifestEntry

logger = logging.getLogger(__name__)

FS = FileSystemHelper


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class ScannedFile:
    """A single file discovered during scanning."""

    path: str
    source_dir: str
    size: int
    mtime: float


@dataclass
class ScanResult:
    """Aggregate result of scanning one or more source directories."""

    files: list[ScannedFile] = field(default_factory=list)
    skipped_hidden: int = 0
    skipped_system: int = 0
    skipped_symlinks: int = 0
    cloud_stubs: list[tuple[str, str]] = field(default_factory=list)
    total_bytes: int = 0
    scan_duration: float = 0.0


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

_TEMP_SUFFIXES = frozenset({".tmp", ".temp"})


class Scanner:
    """Recursive directory scanner with exclusion rules and incremental support."""

    def __init__(
        self,
        config: ConfigManager,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> None:
        self.config = config
        self.progress_callback = progress_callback
        self._visited_inodes: set[tuple[int, int]] = set()  # (dev, ino)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(self, source_dirs: list[str]) -> ScanResult:
        """Scan *source_dirs* recursively and return a :class:`ScanResult`."""
        self._visited_inodes.clear()
        result = ScanResult()
        t0 = time.monotonic()

        for source_dir in source_dirs:
            source_dir = os.path.abspath(source_dir)
            for scanned in self._walk_directory(source_dir, source_dir, result):
                result.files.append(scanned)
                result.total_bytes += scanned.size
                if self.progress_callback is not None:
                    self.progress_callback(len(result.files), scanned.path)

        result.scan_duration = time.monotonic() - t0
        return result

    def scan_incremental(
        self,
        source_dirs: list[str],
        previous_manifest: list[SourceManifestEntry],
    ) -> ScanResult:
        """Return only files that are new or modified compared to *previous_manifest*."""
        lookup: dict[str, SourceManifestEntry] = {
            e.file_path: e for e in previous_manifest
        }

        full = self.scan(source_dirs)

        filtered: list[ScannedFile] = []
        filtered_bytes = 0
        for sf in full.files:
            prev = lookup.get(sf.path)
            if prev is None or prev.file_size != sf.size or prev.mtime != sf.mtime:
                filtered.append(sf)
                filtered_bytes += sf.size

        full.files = filtered
        full.total_bytes = filtered_bytes
        return full

    def build_manifest(
        self, scan_result: ScanResult, session_id: str
    ) -> list[SourceManifestEntry]:
        """Convert a :class:`ScanResult` into database-ready manifest entries."""
        return [
            SourceManifestEntry(
                session_id=session_id,
                source_dir=sf.source_dir,
                file_path=sf.path,
                file_size=sf.size,
                mtime=sf.mtime,
            )
            for sf in scan_result.files
        ]

    # ------------------------------------------------------------------
    # Internal walk
    # ------------------------------------------------------------------

    def _walk_directory(
        self,
        root_dir: str,
        source_dir: str,
        result: ScanResult,
    ) -> Generator[ScannedFile, None, None]:
        """Yield :class:`ScannedFile` for every valid file under *root_dir*.

        Uses :func:`os.scandir` for performance.  All skip / symlink / cloud
        logic lives here.
        """
        follow_symlinks = self.config.follow_symlinks

        try:
            entries = os.scandir(root_dir)
        except PermissionError:
            logger.warning("Permission denied: %s", root_dir)
            return

        with entries:
            for entry in entries:
                name = entry.name

                # When not following symlinks, skip and count any symlink
                # immediately — is_dir/is_file(follow_symlinks=False) returns
                # False for symlinks, so they would otherwise be silently lost.
                if not follow_symlinks and entry.is_symlink():
                    result.skipped_symlinks += 1
                    continue

                # --- directories ---
                if entry.is_dir(follow_symlinks=follow_symlinks):
                    if FS.is_skip_directory(name):
                        continue

                    if FS.is_hidden_or_system(name):
                        continue

                    full = entry.path

                    # symlink handling for directories
                    if entry.is_symlink():
                        if not follow_symlinks:
                            result.skipped_symlinks += 1
                            continue
                        if self._track_inode(full):
                            logger.warning("Symlink cycle detected, skipping: %s", full)
                            continue

                    yield from self._walk_directory(full, source_dir, result)
                    continue

                # --- files ---
                if not entry.is_file(follow_symlinks=follow_symlinks):
                    continue

                full = entry.path

                # symlink check
                if entry.is_symlink():
                    if not follow_symlinks:
                        result.skipped_symlinks += 1
                        continue
                    resolved = FS.resolve_symlink(full)
                    if resolved is None:
                        result.skipped_symlinks += 1
                        continue
                    if self._track_inode(full):
                        logger.warning("Symlink cycle detected, skipping: %s", full)
                        continue

                # hidden / system
                if name.startswith("."):
                    result.skipped_hidden += 1
                    continue

                if name in {"Thumbs.db", "desktop.ini", ".DS_Store"}:
                    result.skipped_system += 1
                    continue

                _, ext = os.path.splitext(name)
                if ext.lower() in _TEMP_SUFFIXES:
                    result.skipped_system += 1
                    continue

                # cloud stubs
                is_stub, svc = FS.is_cloud_stub(full)
                if is_stub:
                    result.cloud_stubs.append((full, svc))
                    continue

                # collect stat
                try:
                    stat = entry.stat(follow_symlinks=follow_symlinks)
                except OSError:
                    logger.warning("Could not stat: %s", full)
                    continue

                yield ScannedFile(
                    path=full,
                    source_dir=source_dir,
                    size=stat.st_size,
                    mtime=stat.st_mtime,
                )

    # ------------------------------------------------------------------
    # Cycle detection
    # ------------------------------------------------------------------

    def _track_inode(self, path: str) -> bool:
        """Return ``True`` if *path*'s inode has already been visited (cycle)."""
        try:
            st = os.stat(path)
            key = (st.st_dev, st.st_ino)
        except OSError:
            return False
        if key in self._visited_inodes:
            return True
        self._visited_inodes.add(key)
        return False
