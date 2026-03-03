"""Tests for sortique.data.file_system.FileSystemHelper."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sortique.data.file_system import FileSystemHelper

FS = FileSystemHelper


# ======================================================================
# atomic_copy
# ======================================================================

class TestAtomicCopy:
    """Verify correct behaviour, temp-file cleanup, and progress reporting."""

    def test_basic_copy_preserves_content(self, tmp_path: Path) -> None:
        src = tmp_path / "src.bin"
        src.write_bytes(b"hello world")
        dst = str(tmp_path / "out" / "dst.bin")

        assert FS.atomic_copy(str(src), dst) is True
        assert Path(dst).read_bytes() == b"hello world"

    def test_metadata_preserved(self, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("data")
        # Set a specific mtime in the past.
        os.utime(str(src), (1_000_000, 1_000_000))

        dst = str(tmp_path / "dst.txt")
        FS.atomic_copy(str(src), dst)
        assert os.path.getmtime(dst) == pytest.approx(1_000_000, abs=1)

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("x")
        dst = str(tmp_path / "a" / "b" / "c" / "dst.txt")
        FS.atomic_copy(str(src), dst)
        assert Path(dst).exists()

    def test_no_leftover_tmp_on_success(self, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("data")
        dst_dir = tmp_path / "out"
        dst_dir.mkdir()
        dst = str(dst_dir / "result.txt")

        FS.atomic_copy(str(src), dst)
        remaining = list(dst_dir.iterdir())
        assert len(remaining) == 1
        assert remaining[0].name == "result.txt"

    def test_no_leftover_tmp_on_failure(self, tmp_path: Path) -> None:
        dst_dir = tmp_path / "out"
        dst_dir.mkdir()
        dst = str(dst_dir / "nope.txt")

        with pytest.raises(FileNotFoundError):
            FS.atomic_copy("/nonexistent/path/file.txt", dst)

        # The destination directory should be empty — no orphaned .tmp.
        assert list(dst_dir.iterdir()) == []

    def test_progress_callback_invoked_for_large_file(self, tmp_path: Path) -> None:
        src = tmp_path / "big.bin"
        # Write just over the threshold so the chunked path is taken.
        size = 100 * 1024 * 1024 + 1
        src.write_bytes(b"\x00" * size)

        dst = str(tmp_path / "big_copy.bin")
        reports: list[tuple[int, int]] = []

        FS.atomic_copy(str(src), dst, progress_callback=lambda c, t: reports.append((c, t)))

        # Must have at least the final 100% report.
        assert len(reports) >= 1
        assert reports[-1] == (size, size)
        assert Path(dst).stat().st_size == size

    def test_progress_callback_not_invoked_for_small_file(self, tmp_path: Path) -> None:
        src = tmp_path / "small.bin"
        src.write_bytes(b"tiny")
        dst = str(tmp_path / "small_copy.bin")

        reports: list[tuple[int, int]] = []
        FS.atomic_copy(str(src), dst, progress_callback=lambda c, t: reports.append((c, t)))
        assert reports == []

    def test_overwrites_existing_destination(self, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("new")
        dst = tmp_path / "dst.txt"
        dst.write_text("old")

        FS.atomic_copy(str(src), str(dst))
        assert dst.read_text() == "new"


# ======================================================================
# sanitize_filename
# ======================================================================

class TestSanitizeFilename:
    """Each target OS, Unicode preservation, and edge cases."""

    # --- Windows ---

    def test_windows_illegal_chars_replaced(self) -> None:
        assert FS.sanitize_filename('a\\b/c:d*e?f"g<h>i|j', "windows") == "a_b_c_d_e_f_g_h_i_j"

    def test_windows_preserves_unicode(self) -> None:
        assert FS.sanitize_filename("café_résumé.txt", "windows") == "café_résumé.txt"

    def test_windows_cjk(self) -> None:
        assert FS.sanitize_filename("日本語ファイル.txt", "windows") == "日本語ファイル.txt"

    def test_windows_emoji(self) -> None:
        result = FS.sanitize_filename("photo_📸_2024.jpg", "windows")
        assert "📸" in result

    # --- Linux ---

    def test_linux_slash_replaced(self) -> None:
        assert FS.sanitize_filename("a/b/c.txt", "linux") == "a_b_c.txt"

    def test_linux_null_replaced(self) -> None:
        assert FS.sanitize_filename("a\x00b.txt", "linux") == "a_b.txt"

    def test_linux_allows_colon(self) -> None:
        assert FS.sanitize_filename("12:30:00.txt", "linux") == "12:30:00.txt"

    # --- macOS ---

    def test_macos_colon_and_slash_replaced(self) -> None:
        assert FS.sanitize_filename("2024/06/15:photo.jpg", "macos") == "2024_06_15_photo.jpg"

    def test_macos_allows_other_special_chars(self) -> None:
        assert FS.sanitize_filename("file (1) [copy].jpg", "macos") == "file (1) [copy].jpg"

    # --- Edge cases ---

    def test_leading_trailing_whitespace_stripped(self) -> None:
        assert FS.sanitize_filename("  hello.txt  ", "linux") == "hello.txt"

    def test_leading_trailing_dots_stripped(self) -> None:
        assert FS.sanitize_filename("...hidden...", "linux") == "hidden"

    def test_empty_after_sanitization(self) -> None:
        assert FS.sanitize_filename("...", "linux") == "unnamed"

    def test_all_illegal_chars_replaced(self) -> None:
        # All illegal chars become underscores — still a valid filename.
        assert FS.sanitize_filename('\\/:*?"<>|', "windows") == "_________"

    def test_dots_only_yields_unnamed(self) -> None:
        # After replacing nothing and stripping dots, result is empty.
        assert FS.sanitize_filename("...", "windows") == "unnamed"

    def test_only_whitespace_yields_unnamed(self) -> None:
        assert FS.sanitize_filename("   ", "linux") == "unnamed"

    def test_auto_detect_does_not_crash(self) -> None:
        result = FS.sanitize_filename("test:file.txt")
        assert isinstance(result, str)
        assert len(result) > 0


# ======================================================================
# is_hidden_or_system
# ======================================================================

class TestIsHiddenOrSystem:
    def test_dot_prefix(self) -> None:
        assert FS.is_hidden_or_system("/some/path/.hidden") is True

    def test_ds_store(self) -> None:
        assert FS.is_hidden_or_system("/Users/me/photos/.DS_Store") is True

    def test_thumbs_db(self) -> None:
        assert FS.is_hidden_or_system("C:\\folder\\Thumbs.db") is True

    def test_desktop_ini(self) -> None:
        assert FS.is_hidden_or_system("desktop.ini") is True

    def test_normal_file(self) -> None:
        assert FS.is_hidden_or_system("/photos/IMG_1234.jpg") is False

    def test_dot_in_middle_not_hidden(self) -> None:
        assert FS.is_hidden_or_system("/some/my.file.txt") is False

    def test_empty_basename_dot(self) -> None:
        # A path whose basename is "." — technically starts with ".".
        assert FS.is_hidden_or_system(".") is True


# ======================================================================
# is_skip_directory
# ======================================================================

class TestIsSkipDirectory:
    def test_git(self) -> None:
        assert FS.is_skip_directory(".git") is True

    def test_node_modules(self) -> None:
        assert FS.is_skip_directory("node_modules") is True

    def test_pycache_full_path(self) -> None:
        assert FS.is_skip_directory("/project/src/__pycache__") is True

    def test_normal_directory(self) -> None:
        assert FS.is_skip_directory("photos") is False


# ======================================================================
# remove_empty_parents
# ======================================================================

class TestRemoveEmptyParents:
    def test_removes_empty_chain(self, tmp_path: Path) -> None:
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)

        # Point at a (fictitious) file inside the deepest dir.
        file_path = str(deep / "file.txt")
        FS.remove_empty_parents(file_path, str(tmp_path))

        # a, b, c should all be gone.
        assert not (tmp_path / "a").exists()

    def test_stops_at_boundary(self, tmp_path: Path) -> None:
        stop = tmp_path / "root"
        deep = stop / "x" / "y"
        deep.mkdir(parents=True)

        FS.remove_empty_parents(str(deep / "f.txt"), str(stop))

        # "x" and "y" removed, but "root" must survive.
        assert stop.exists()
        assert not (stop / "x").exists()

    def test_stops_at_non_empty_parent(self, tmp_path: Path) -> None:
        chain = tmp_path / "a" / "b" / "c"
        chain.mkdir(parents=True)
        # Put a sibling inside "a" so it isn't empty.
        (tmp_path / "a" / "keep.txt").write_text("keep")

        FS.remove_empty_parents(str(chain / "f.txt"), str(tmp_path))

        assert (tmp_path / "a").exists()       # non-empty, kept
        assert not (tmp_path / "a" / "b").exists()  # empty, removed

    def test_noop_when_already_at_stop(self, tmp_path: Path) -> None:
        FS.remove_empty_parents(str(tmp_path / "f.txt"), str(tmp_path))
        assert tmp_path.exists()


# ======================================================================
# Miscellaneous helpers
# ======================================================================

class TestMiscHelpers:
    def test_get_free_space(self, tmp_path: Path) -> None:
        free = FS.get_free_space(str(tmp_path))
        assert isinstance(free, int)
        assert free > 0

    def test_get_file_size_and_mtime(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_bytes(b"abcdef")
        assert FS.get_file_size(str(f)) == 6
        assert FS.get_file_mtime(str(f)) > 0

    def test_files_match_positive(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_bytes(b"data")
        size = FS.get_file_size(str(f))
        mtime = FS.get_file_mtime(str(f))
        assert FS.files_match(str(f), size, mtime) is True

    def test_files_match_negative(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_bytes(b"data")
        assert FS.files_match(str(f), 999, 0.0) is False

    def test_files_match_missing(self) -> None:
        assert FS.files_match("/no/such/file", 0, 0.0) is False

    def test_ensure_directory(self, tmp_path: Path) -> None:
        d = str(tmp_path / "a" / "b" / "c")
        FS.ensure_directory(d)
        assert os.path.isdir(d)

    def test_is_symlink_false_for_regular(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("x")
        assert FS.is_symlink(str(f)) is False

    def test_resolve_symlink_returns_none_for_broken(self, tmp_path: Path) -> None:
        link = tmp_path / "broken"
        try:
            link.symlink_to(tmp_path / "nonexistent")
        except OSError:
            pytest.skip("symlinks not supported or not permitted")
        assert FS.resolve_symlink(str(link)) is None

    def test_cloud_stub_normal_file(self, tmp_path: Path) -> None:
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"\xff\xd8")
        is_stub, svc = FS.is_cloud_stub(str(f))
        assert is_stub is False
        assert svc == ""

    def test_cloud_stub_icloud_extension(self, tmp_path: Path) -> None:
        f = tmp_path / "photo.jpg.icloud"
        f.write_bytes(b"stub")
        is_stub, svc = FS.is_cloud_stub(str(f))
        assert is_stub is True
        assert svc == "icloud"

    def test_cloud_stub_gdrive_zero_byte(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.gdoc"
        f.write_bytes(b"")  # zero-byte
        is_stub, svc = FS.is_cloud_stub(str(f))
        assert is_stub is True
        assert svc == "gdrive"

    def test_cloud_stub_gdrive_nonzero_byte(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.gdoc"
        f.write_bytes(b"content")  # non-zero — not a stub
        is_stub, _ = FS.is_cloud_stub(str(f))
        assert is_stub is False
