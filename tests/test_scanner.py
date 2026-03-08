"""Tests for sortique.engine.scanner.Scanner."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sortique.data.config_manager import ConfigManager
from sortique.data.models import SourceManifestEntry
from sortique.engine.scanner import ScanResult, ScannedFile, Scanner


# ======================================================================
# Fixtures
# ======================================================================

@pytest.fixture()
def config(tmp_path: Path) -> ConfigManager:
    """ConfigManager pointed at a throwaway directory."""
    return ConfigManager(config_dir=str(tmp_path / "cfg"))


@pytest.fixture()
def scan_tree(tmp_path: Path) -> Path:
    """Build a representative directory tree for testing.

    Layout::

        scan_test/
        ├── photos/
        │   ├── vacation.jpg          (normal image)
        │   ├── family.png            (normal image)
        │   └── raw/
        │       └── IMG_001.cr2       (normal RAW)
        ├── docs/
        │   └── notes.txt             (normal document)
        ├── .hidden_file              (hidden — dot prefix)
        ├── .hidden_dir/
        │   └── secret.txt            (inside hidden dir, not reached)
        ├── Thumbs.db                 (system file)
        ├── desktop.ini               (system file)
        ├── .DS_Store                 (hidden + system)
        ├── temp_stuff.tmp            (temp extension)
        ├── cache.temp                (temp extension)
        ├── .git/
        │   ├── HEAD                  (skip-dir contents)
        │   └── config                (skip-dir contents)
        ├── node_modules/
        │   └── pkg/
        │       └── index.js          (skip-dir contents)
        └── stub.icloud               (cloud stub)
    """
    root = tmp_path / "scan_test"

    # Normal files
    _touch(root / "photos" / "vacation.jpg", b"\xff\xd8photo1")
    _touch(root / "photos" / "family.png", b"\x89PNGphoto2")
    _touch(root / "photos" / "raw" / "IMG_001.cr2", b"rawdata")
    _touch(root / "docs" / "notes.txt", b"hello world")

    # Hidden
    _touch(root / ".hidden_file", b"hidden")
    _touch(root / ".hidden_dir" / "secret.txt", b"secret")

    # System
    _touch(root / "Thumbs.db", b"thumbs")
    _touch(root / "desktop.ini", b"[.ShellClassInfo]")
    _touch(root / ".DS_Store", b"\x00\x00\x00\x01")

    # Temp
    _touch(root / "temp_stuff.tmp", b"tmp")
    _touch(root / "cache.temp", b"temp")

    # Skip dirs
    _touch(root / ".git" / "HEAD", b"ref: refs/heads/main")
    _touch(root / ".git" / "config", b"[core]")
    _touch(root / "node_modules" / "pkg" / "index.js", b"module.exports={}")

    # Cloud stub
    _touch(root / "stub.icloud", b"stub")

    # Android thumbnail cache files (contain JPEG data but are cache artefacts)
    _touch(root / "photos" / "43472faf4de4b1fff3e461ec160337c0.thumb1", b"\xff\xd8\xffthumb")
    _touch(root / "photos" / "cc3c44f57e626e30c879a5805f8899d6.thumb0", b"\xff\xd8\xffthumb")
    _touch(root / "photos" / "cbff4b798cf25336ba53904a962e182d.thumb", b"\xff\xd8\xffthumb")
    _touch(root / "photos" / "cc3c44f57e626e30c879a5805f8899d6.thumb2", b"\xff\xd8\xffthumb")

    return root


def _touch(path: Path, data: bytes = b"") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


# ======================================================================
# Basic scan
# ======================================================================

class TestBasicScan:
    def test_finds_all_normal_files(self, config: ConfigManager, scan_tree: Path) -> None:
        scanner = Scanner(config)
        result = scanner.scan([str(scan_tree)])

        found = {os.path.basename(f.path) for f in result.files}
        assert found == {"vacation.jpg", "family.png", "IMG_001.cr2", "notes.txt"}

    def test_total_bytes_matches(self, config: ConfigManager, scan_tree: Path) -> None:
        scanner = Scanner(config)
        result = scanner.scan([str(scan_tree)])
        assert result.total_bytes == sum(f.size for f in result.files)
        assert result.total_bytes > 0

    def test_scan_duration_recorded(self, config: ConfigManager, scan_tree: Path) -> None:
        scanner = Scanner(config)
        result = scanner.scan([str(scan_tree)])
        assert result.scan_duration > 0

    def test_source_dir_tracked(self, config: ConfigManager, scan_tree: Path) -> None:
        scanner = Scanner(config)
        src = str(scan_tree)
        result = scanner.scan([src])
        for f in result.files:
            assert f.source_dir == src

    def test_multiple_source_dirs(self, config: ConfigManager, scan_tree: Path) -> None:
        scanner = Scanner(config)
        photos = str(scan_tree / "photos")
        docs = str(scan_tree / "docs")
        result = scanner.scan([photos, docs])

        found = {os.path.basename(f.path) for f in result.files}
        assert "vacation.jpg" in found
        assert "notes.txt" in found

        dirs = {f.source_dir for f in result.files}
        assert dirs == {photos, docs}

    def test_progress_callback_invoked(self, config: ConfigManager, scan_tree: Path) -> None:
        reports: list[tuple[int, str]] = []
        scanner = Scanner(config, progress_callback=lambda n, p: reports.append((n, p)))
        result = scanner.scan([str(scan_tree)])
        assert len(reports) == len(result.files)
        # Counts should be monotonically increasing.
        assert [r[0] for r in reports] == list(range(1, len(result.files) + 1))

    def test_empty_directory(self, config: ConfigManager, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        scanner = Scanner(config)
        result = scanner.scan([str(empty)])
        assert result.files == []
        assert result.total_bytes == 0


# ======================================================================
# Hidden file skipping
# ======================================================================

class TestHiddenSkipping:
    def test_dot_files_skipped(self, config: ConfigManager, scan_tree: Path) -> None:
        scanner = Scanner(config)
        result = scanner.scan([str(scan_tree)])
        names = {os.path.basename(f.path) for f in result.files}
        assert ".hidden_file" not in names
        assert ".DS_Store" not in names

    def test_hidden_dir_contents_not_reached(self, config: ConfigManager, scan_tree: Path) -> None:
        scanner = Scanner(config)
        result = scanner.scan([str(scan_tree)])
        paths = {f.path for f in result.files}
        assert not any("secret.txt" in p for p in paths)

    def test_skipped_hidden_count(self, config: ConfigManager, scan_tree: Path) -> None:
        scanner = Scanner(config)
        result = scanner.scan([str(scan_tree)])
        # .hidden_file and .DS_Store are dot-prefixed files at root level.
        # .DS_Store starts with "." so it goes to hidden counter.
        assert result.skipped_hidden >= 1


# ======================================================================
# System file skipping
# ======================================================================

class TestSystemFileSkipping:
    def test_thumbs_db_skipped(self, config: ConfigManager, scan_tree: Path) -> None:
        scanner = Scanner(config)
        result = scanner.scan([str(scan_tree)])
        names = {os.path.basename(f.path) for f in result.files}
        assert "Thumbs.db" not in names

    def test_desktop_ini_skipped(self, config: ConfigManager, scan_tree: Path) -> None:
        scanner = Scanner(config)
        result = scanner.scan([str(scan_tree)])
        names = {os.path.basename(f.path) for f in result.files}
        assert "desktop.ini" not in names

    def test_tmp_files_skipped(self, config: ConfigManager, scan_tree: Path) -> None:
        scanner = Scanner(config)
        result = scanner.scan([str(scan_tree)])
        names = {os.path.basename(f.path) for f in result.files}
        assert "temp_stuff.tmp" not in names
        assert "cache.temp" not in names

    def test_skipped_system_count(self, config: ConfigManager, scan_tree: Path) -> None:
        scanner = Scanner(config)
        result = scanner.scan([str(scan_tree)])
        # Thumbs.db, desktop.ini, temp_stuff.tmp, cache.temp, + 4 .thumb* = 8
        assert result.skipped_system >= 8


# ======================================================================
# Cache file skipping (.thumb*)
# ======================================================================

class TestCacheFileSkipping:
    def test_thumb_files_skipped(self, config: ConfigManager, scan_tree: Path) -> None:
        scanner = Scanner(config)
        result = scanner.scan([str(scan_tree)])
        names = {os.path.basename(f.path) for f in result.files}
        assert "43472faf4de4b1fff3e461ec160337c0.thumb1" not in names
        assert "cc3c44f57e626e30c879a5805f8899d6.thumb0" not in names
        assert "cbff4b798cf25336ba53904a962e182d.thumb" not in names
        assert "cc3c44f57e626e30c879a5805f8899d6.thumb2" not in names

    def test_thumb_counted_as_system_skip(self, config: ConfigManager, tmp_path: Path) -> None:
        """Each .thumb* file increments skipped_system."""
        root = tmp_path / "thumb_only"
        _touch(root / "abc123.thumb1", b"\xff\xd8\xffcache")
        _touch(root / "def456.thumb0", b"\xff\xd8\xffcache")
        _touch(root / "photo.jpg", b"\xff\xd8\xffreal")

        scanner = Scanner(config)
        result = scanner.scan([str(root)])

        assert len(result.files) == 1
        assert os.path.basename(result.files[0].path) == "photo.jpg"
        assert result.skipped_system == 2

    def test_thumb_case_insensitive(self, config: ConfigManager, tmp_path: Path) -> None:
        """Extension check is case-insensitive."""
        root = tmp_path / "thumb_case"
        _touch(root / "ABC.THUMB1", b"\xff\xd8\xffcache")
        _touch(root / "DEF.Thumb", b"\xff\xd8\xffcache")

        scanner = Scanner(config)
        result = scanner.scan([str(root)])

        assert result.files == []
        assert result.skipped_system == 2

    def test_thumb_with_higher_numbers(self, config: ConfigManager, tmp_path: Path) -> None:
        """Handles .thumb3, .thumb4, etc. via startswith matching."""
        root = tmp_path / "thumb_nums"
        _touch(root / "cache.thumb3", b"data")
        _touch(root / "cache.thumb10", b"data")
        _touch(root / "cache.thumbx", b"data")  # unusual but still .thumb*

        scanner = Scanner(config)
        result = scanner.scan([str(root)])

        assert result.files == []
        assert result.skipped_system == 3


# ======================================================================
# Directory exclusion
# ======================================================================

def _has_path_component(paths: set[str], component: str) -> bool:
    """True if *component* appears as an actual directory name in any path."""
    for p in paths:
        parts = Path(p).parts
        if component in parts:
            return True
    return False


class TestDirectoryExclusion:
    def test_git_dir_skipped(self, config: ConfigManager, scan_tree: Path) -> None:
        scanner = Scanner(config)
        result = scanner.scan([str(scan_tree)])
        paths = {f.path for f in result.files}
        assert not _has_path_component(paths, ".git")

    def test_node_modules_skipped(self, config: ConfigManager, scan_tree: Path) -> None:
        scanner = Scanner(config)
        result = scanner.scan([str(scan_tree)])
        paths = {f.path for f in result.files}
        assert not _has_path_component(paths, "node_modules")


# ======================================================================
# Symlink skipping (default follow_symlinks=False)
# ======================================================================

class TestSymlinkSkipping:
    def test_symlink_files_skipped_by_default(
        self, config: ConfigManager, tmp_path: Path
    ) -> None:
        root = tmp_path / "sym_test"
        real = root / "real.txt"
        _touch(real, b"data")
        link = root / "link.txt"
        try:
            link.symlink_to(real)
        except OSError:
            pytest.skip("symlinks not supported or not permitted")

        scanner = Scanner(config)
        result = scanner.scan([str(root)])

        names = {os.path.basename(f.path) for f in result.files}
        assert "real.txt" in names
        assert "link.txt" not in names
        assert result.skipped_symlinks >= 1

    def test_symlink_dirs_skipped_by_default(
        self, config: ConfigManager, tmp_path: Path
    ) -> None:
        root = tmp_path / "sym_dir_test"
        real_dir = root / "real_dir"
        _touch(real_dir / "inside.txt", b"data")
        link_dir = root / "link_dir"
        try:
            link_dir.symlink_to(real_dir, target_is_directory=True)
        except OSError:
            pytest.skip("symlinks not supported or not permitted")

        scanner = Scanner(config)
        result = scanner.scan([str(root)])

        names = {os.path.basename(f.path) for f in result.files}
        assert "inside.txt" in names
        # Only one copy — from real_dir, not from link_dir.
        assert sum(1 for f in result.files if f.path.endswith("inside.txt")) == 1
        assert result.skipped_symlinks >= 1


# ======================================================================
# Cloud stub detection
# ======================================================================

class TestCloudStubs:
    def test_icloud_stub_detected(self, config: ConfigManager, scan_tree: Path) -> None:
        scanner = Scanner(config)
        result = scanner.scan([str(scan_tree)])

        names = {os.path.basename(f.path) for f in result.files}
        assert "stub.icloud" not in names

        stub_names = {os.path.basename(p) for p, _ in result.cloud_stubs}
        assert "stub.icloud" in stub_names

    def test_cloud_stub_info(self, config: ConfigManager, scan_tree: Path) -> None:
        scanner = Scanner(config)
        result = scanner.scan([str(scan_tree)])
        services = {svc for _, svc in result.cloud_stubs}
        assert "icloud" in services


# ======================================================================
# Incremental scan
# ======================================================================

class TestIncrementalScan:
    def test_unchanged_files_excluded(self, config: ConfigManager, scan_tree: Path) -> None:
        scanner = Scanner(config)
        full = scanner.scan([str(scan_tree)])

        # Build a manifest from the full scan.
        manifest = scanner.build_manifest(full, session_id="s1")

        # Re-scan incrementally — nothing changed, so no files returned.
        incr = scanner.scan_incremental([str(scan_tree)], manifest)
        assert incr.files == []

    def test_new_file_included(self, config: ConfigManager, scan_tree: Path) -> None:
        scanner = Scanner(config)
        full = scanner.scan([str(scan_tree)])
        manifest = scanner.build_manifest(full, session_id="s1")

        # Add a new file.
        _touch(scan_tree / "photos" / "new_photo.jpg", b"brand new")

        incr = scanner.scan_incremental([str(scan_tree)], manifest)
        names = {os.path.basename(f.path) for f in incr.files}
        assert "new_photo.jpg" in names
        # Original files should NOT be in the incremental result.
        assert "vacation.jpg" not in names

    def test_modified_file_included(self, config: ConfigManager, scan_tree: Path) -> None:
        scanner = Scanner(config)
        full = scanner.scan([str(scan_tree)])
        manifest = scanner.build_manifest(full, session_id="s1")

        # Modify an existing file (change size).
        target = scan_tree / "docs" / "notes.txt"
        target.write_bytes(b"hello world -- updated with more data!")

        incr = scanner.scan_incremental([str(scan_tree)], manifest)
        names = {os.path.basename(f.path) for f in incr.files}
        assert "notes.txt" in names


# ======================================================================
# build_manifest
# ======================================================================

class TestBuildManifest:
    def test_manifest_entries_match_scan(self, config: ConfigManager, scan_tree: Path) -> None:
        scanner = Scanner(config)
        result = scanner.scan([str(scan_tree)])
        manifest = scanner.build_manifest(result, session_id="sess-42")

        assert len(manifest) == len(result.files)
        for entry in manifest:
            assert entry.session_id == "sess-42"
            assert entry.file_size > 0
            assert entry.mtime > 0
            assert entry.source_dir != ""

    def test_manifest_paths_absolute(self, config: ConfigManager, scan_tree: Path) -> None:
        scanner = Scanner(config)
        result = scanner.scan([str(scan_tree)])
        manifest = scanner.build_manifest(result, session_id="s1")
        for entry in manifest:
            assert os.path.isabs(entry.file_path)
