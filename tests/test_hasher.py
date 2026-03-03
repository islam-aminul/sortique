"""Tests for sortique.engine.hasher.FileHasher."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from sortique.engine.hasher import FileHasher

hasher = FileHasher()


# ======================================================================
# Hash consistency
# ======================================================================

class TestHashConsistency:
    def test_same_file_same_hash(self, tmp_path: Path) -> None:
        f = tmp_path / "a.bin"
        f.write_bytes(b"hello world")
        h1 = hasher.hash_file(str(f))
        h2 = hasher.hash_file(str(f))
        assert h1 == h2

    def test_identical_content_same_hash(self, tmp_path: Path) -> None:
        data = b"identical content bytes"
        f1 = tmp_path / "f1.bin"
        f2 = tmp_path / "f2.bin"
        f1.write_bytes(data)
        f2.write_bytes(data)
        assert hasher.hash_file(str(f1)) == hasher.hash_file(str(f2))

    def test_matches_hashlib_directly(self, tmp_path: Path) -> None:
        data = os.urandom(4096)
        f = tmp_path / "rand.bin"
        f.write_bytes(data)
        expected = hashlib.sha256(data).hexdigest()
        assert hasher.hash_file(str(f)) == expected

    def test_hash_is_lowercase_hex(self, tmp_path: Path) -> None:
        f = tmp_path / "x.bin"
        f.write_bytes(b"test")
        h = hasher.hash_file(str(f))
        assert len(h) == 64
        assert h == h.lower()
        assert all(c in "0123456789abcdef" for c in h)

    def test_empty_file_hash(self, tmp_path: Path) -> None:
        f = tmp_path / "empty"
        f.write_bytes(b"")
        expected = hashlib.sha256(b"").hexdigest()
        assert hasher.hash_file(str(f)) == expected


# ======================================================================
# Different files → different hashes
# ======================================================================

class TestDifferentHashes:
    def test_different_content_different_hash(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.bin"
        f2 = tmp_path / "b.bin"
        f1.write_bytes(b"alpha")
        f2.write_bytes(b"bravo")
        assert hasher.hash_file(str(f1)) != hasher.hash_file(str(f2))

    def test_one_byte_difference(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.bin"
        f2 = tmp_path / "b.bin"
        f1.write_bytes(b"\x00" * 1024)
        f2.write_bytes(b"\x00" * 1023 + b"\x01")
        assert hasher.hash_file(str(f1)) != hasher.hash_file(str(f2))


# ======================================================================
# verify_copy
# ======================================================================

class TestVerifyCopy:
    def test_matching_files(self, tmp_path: Path) -> None:
        data = os.urandom(2048)
        src = tmp_path / "src.bin"
        dst = tmp_path / "dst.bin"
        src.write_bytes(data)
        dst.write_bytes(data)
        assert hasher.verify_copy(str(src), str(dst)) is True

    def test_non_matching_files(self, tmp_path: Path) -> None:
        src = tmp_path / "src.bin"
        dst = tmp_path / "dst.bin"
        src.write_bytes(b"original")
        dst.write_bytes(b"corrupted")
        assert hasher.verify_copy(str(src), str(dst)) is False


# ======================================================================
# quick_compare
# ======================================================================

class TestQuickCompare:
    def test_size_mismatch_short_circuits(self, tmp_path: Path) -> None:
        f1 = tmp_path / "small.bin"
        f2 = tmp_path / "big.bin"
        f1.write_bytes(b"short")
        f2.write_bytes(b"much longer content here")

        with patch.object(hasher, "hash_file", wraps=hasher.hash_file) as mock_hash:
            result = hasher.quick_compare(str(f1), str(f2))
            assert result is False
            mock_hash.assert_not_called()

    def test_same_size_same_content(self, tmp_path: Path) -> None:
        data = b"same content"
        f1 = tmp_path / "a.bin"
        f2 = tmp_path / "b.bin"
        f1.write_bytes(data)
        f2.write_bytes(data)
        assert hasher.quick_compare(str(f1), str(f2)) is True

    def test_same_size_different_content(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.bin"
        f2 = tmp_path / "b.bin"
        f1.write_bytes(b"aaaa")
        f2.write_bytes(b"bbbb")
        assert hasher.quick_compare(str(f1), str(f2)) is False


# ======================================================================
# Progress callback for large files
# ======================================================================

class TestProgressCallback:
    def test_callback_invoked_for_large_file(self, tmp_path: Path) -> None:
        """Write a file just over LARGE_FILE_THRESHOLD and verify progress reports."""
        size = 100 * 1024 * 1024 + 1  # 1 byte over 100 MB
        f = tmp_path / "large.bin"
        f.write_bytes(b"\x00" * size)

        reports: list[tuple[int, int]] = []
        hasher.hash_file(str(f), progress_callback=lambda c, t: reports.append((c, t)))

        assert len(reports) >= 1
        # Final report must show total == total.
        assert reports[-1] == (size, size)
        # Every report must have correct total.
        assert all(t == size for _, t in reports)
        # bytes_hashed must be monotonically increasing.
        bytes_seq = [c for c, _ in reports]
        assert bytes_seq == sorted(bytes_seq)

    def test_callback_not_invoked_for_small_file(self, tmp_path: Path) -> None:
        f = tmp_path / "small.bin"
        f.write_bytes(b"small data")

        reports: list[tuple[int, int]] = []
        hasher.hash_file(str(f), progress_callback=lambda c, t: reports.append((c, t)))
        assert reports == []

    def test_callback_none_no_error(self, tmp_path: Path) -> None:
        f = tmp_path / "f.bin"
        f.write_bytes(b"data")
        # Must not raise when callback is None.
        h = hasher.hash_file(str(f), progress_callback=None)
        assert len(h) == 64


# ======================================================================
# Batch hashing
# ======================================================================

class TestBatchHashing:
    def test_batch_returns_all(self, tmp_path: Path) -> None:
        paths = []
        for i in range(5):
            f = tmp_path / f"f{i}.bin"
            f.write_bytes(f"content-{i}".encode())
            paths.append(str(f))

        result = hasher.hash_files_batch(paths)
        assert set(result.keys()) == set(paths)
        assert all(len(v) == 64 for v in result.values())

    def test_batch_progress_callback(self, tmp_path: Path) -> None:
        paths = []
        for i in range(3):
            f = tmp_path / f"f{i}.bin"
            f.write_bytes(f"data-{i}".encode())
            paths.append(str(f))

        reports: list[tuple[int, int, str]] = []
        hasher.hash_files_batch(
            paths, progress_callback=lambda done, total, fp: reports.append((done, total, fp))
        )

        assert len(reports) == 3
        assert [r[0] for r in reports] == [1, 2, 3]
        assert all(r[1] == 3 for r in reports)

    def test_batch_empty(self) -> None:
        assert hasher.hash_files_batch([]) == {}
