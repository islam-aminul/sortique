"""Portable hash manifest for cross-machine / cross-user deduplication.

Stores SHA-256 → relative-path mappings in a lightweight SQLite database
at ``{destination}/.sortique/hash_manifest.db``.  This file travels with
the destination directory (e.g. on an external drive), enabling any
Sortique instance to detect files that were already organised — even on
a different computer or under a different user account.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

MANIFEST_DIR = ".sortique"
MANIFEST_DB = "hash_manifest.db"


class HashManifest:
    """Thread-safe portable hash manifest backed by SQLite.

    The database is created (or opened) at
    ``{destination_dir}/.sortique/hash_manifest.db``.

    All public methods are safe to call from multiple threads and are
    wrapped in ``try / except`` so that manifest errors **never** crash
    the main pipeline.
    """

    def __init__(self, destination_dir: str) -> None:
        self._dest_dir = destination_dir
        manifest_dir = os.path.join(destination_dir, MANIFEST_DIR)
        os.makedirs(manifest_dir, exist_ok=True)
        self._db_path = os.path.join(manifest_dir, MANIFEST_DB)

        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_table()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _create_table(self) -> None:
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS hashes (
                   sha256_hash  TEXT PRIMARY KEY,
                   rel_path     TEXT NOT NULL,
                   file_size    INTEGER NOT NULL DEFAULT 0,
                   added_at     TEXT NOT NULL
               )"""
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_all(self) -> dict[str, str]:
        """Return ``{sha256_hash: rel_path}`` for every entry.

        Returns an empty dict on error.
        """
        try:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT sha256_hash, rel_path FROM hashes",
                ).fetchall()
                return {row[0]: row[1] for row in rows}
        except Exception:
            logger.warning("Failed to load hash manifest", exc_info=True)
            return {}

    def add(self, sha256_hash: str, rel_path: str, file_size: int) -> None:
        """Add or replace a hash entry.  Never raises."""
        try:
            with self._lock:
                self._conn.execute(
                    """INSERT OR REPLACE INTO hashes
                           (sha256_hash, rel_path, file_size, added_at)
                       VALUES (?, ?, ?, ?)""",
                    (
                        sha256_hash,
                        rel_path,
                        file_size,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                self._conn.commit()
        except Exception:
            logger.warning("Failed to write hash manifest entry", exc_info=True)

    def remove(self, sha256_hash: str) -> None:
        """Delete a hash entry.  Never raises."""
        try:
            with self._lock:
                self._conn.execute(
                    "DELETE FROM hashes WHERE sha256_hash = ?",
                    (sha256_hash,),
                )
                self._conn.commit()
        except Exception:
            logger.warning("Failed to remove hash manifest entry", exc_info=True)

    def close(self) -> None:
        """Close the database connection."""
        try:
            self._conn.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Class helpers
    # ------------------------------------------------------------------

    @classmethod
    def exists(cls, destination_dir: str) -> bool:
        """Return ``True`` if a manifest database exists at *destination_dir*."""
        return os.path.exists(
            os.path.join(destination_dir, MANIFEST_DIR, MANIFEST_DB),
        )
