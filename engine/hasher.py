"""SHA-256 file hashing with streaming support for large files."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable

from sortique.constants import LARGE_FILE_THRESHOLD


class FileHasher:
    """SHA-256 file hashing with streaming support for large files."""

    CHUNK_SIZE: int = 8 * 1024 * 1024  # 8 MB

    def hash_file(
        self,
        filepath: str,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> str:
        """Return the lowercase hex SHA-256 digest of *filepath*.

        Reads in ``CHUNK_SIZE`` chunks.  When the file exceeds
        ``LARGE_FILE_THRESHOLD`` and *progress_callback* is provided it is
        called as ``progress_callback(bytes_hashed, total_bytes)`` after each
        chunk.
        """
        total = os.path.getsize(filepath)
        report = total > LARGE_FILE_THRESHOLD and progress_callback is not None

        h = hashlib.sha256()
        hashed = 0
        with open(filepath, "rb") as f:
            while True:
                buf = f.read(self.CHUNK_SIZE)
                if not buf:
                    break
                h.update(buf)
                hashed += len(buf)
                if report:
                    progress_callback(hashed, total)  # type: ignore[misc]

        return h.hexdigest()

    def hash_files_batch(
        self,
        filepaths: list[str],
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> dict[str, str]:
        """Hash every file in *filepaths*.

        Returns ``{filepath: hex_digest}``.  *progress_callback* receives
        ``(files_completed, total_files, current_filepath)`` after each file.
        """
        total = len(filepaths)
        result: dict[str, str] = {}
        for idx, fp in enumerate(filepaths, 1):
            result[fp] = self.hash_file(fp)
            if progress_callback is not None:
                progress_callback(idx, total, fp)
        return result

    def verify_copy(self, source_path: str, dest_path: str) -> bool:
        """Return ``True`` when *source_path* and *dest_path* have identical SHA-256 digests."""
        return self.hash_file(source_path) == self.hash_file(dest_path)

    def quick_compare(self, filepath1: str, filepath2: str) -> bool:
        """Size-check shortcut followed by full hash comparison.

        Returns ``False`` immediately if the two files differ in size,
        avoiding the cost of hashing.
        """
        if os.path.getsize(filepath1) != os.path.getsize(filepath2):
            return False
        return self.hash_file(filepath1) == self.hash_file(filepath2)
