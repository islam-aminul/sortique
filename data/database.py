"""Sortique SQLite database manager."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Generator

from sortique.constants import (
    DateSource,
    DupMatchType,
    ExifStatus,
    FileStatus,
    FileType,
    PairPolicy,
    SessionState,
)
from sortique.data.models import (
    DuplicateGroup,
    FileRecord,
    Session,
    SourceManifestEntry,
)


class Database:
    """SQLite-backed persistence layer for Sortique."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Thread-safe read query."""
        with self._lock:
            return self._conn.execute(sql, params)

    @contextmanager
    def _transaction(self) -> Generator[sqlite3.Cursor, None, None]:
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("BEGIN")
                yield cur
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def _create_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id              TEXT PRIMARY KEY,
                state           TEXT NOT NULL,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL,
                source_dirs     TEXT NOT NULL,
                destination_dir TEXT NOT NULL,
                config_snapshot TEXT NOT NULL,
                stats           TEXT NOT NULL,
                is_archived     INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS file_records (
                id                  TEXT PRIMARY KEY,
                session_id          TEXT NOT NULL,
                source_path         TEXT NOT NULL,
                source_dir          TEXT NOT NULL,
                destination_path    TEXT,
                file_type           TEXT NOT NULL,
                content_type        TEXT NOT NULL DEFAULT '',
                category            TEXT NOT NULL DEFAULT '',
                file_size           INTEGER NOT NULL DEFAULT 0,
                sha256_hash         TEXT,
                perceptual_hash     TEXT,
                pipeline_stage      INTEGER NOT NULL DEFAULT 1,
                status              TEXT NOT NULL,
                skip_reason         TEXT,
                error_message       TEXT,
                date_value          TEXT,
                date_source         TEXT NOT NULL DEFAULT 'none',
                timezone_offset     TEXT,
                exif_status         TEXT NOT NULL DEFAULT 'none',
                exif_data           TEXT,
                is_duplicate        INTEGER NOT NULL DEFAULT 0,
                duplicate_group_id  TEXT,
                pair_id             TEXT,
                pair_policy         TEXT,
                verified            INTEGER NOT NULL DEFAULT 0,
                created_at          TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );

            CREATE TABLE IF NOT EXISTS duplicate_groups (
                id              TEXT PRIMARY KEY,
                session_id      TEXT NOT NULL,
                winner_file_id  TEXT NOT NULL,
                hash_value      TEXT NOT NULL,
                match_type      TEXT NOT NULL,
                file_count      INTEGER NOT NULL DEFAULT 0,
                bytes_saved     INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );

            CREATE TABLE IF NOT EXISTS source_manifest (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL,
                source_dir  TEXT NOT NULL,
                file_path   TEXT NOT NULL,
                file_size   INTEGER NOT NULL DEFAULT 0,
                mtime       REAL NOT NULL DEFAULT 0,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );

            -- file_records indexes
            CREATE INDEX IF NOT EXISTS idx_fr_session_status
                ON file_records(session_id, status);
            CREATE INDEX IF NOT EXISTS idx_fr_sha256
                ON file_records(sha256_hash);
            CREATE INDEX IF NOT EXISTS idx_fr_session_stage
                ON file_records(session_id, pipeline_stage);
            CREATE INDEX IF NOT EXISTS idx_fr_dup_group
                ON file_records(duplicate_group_id);
            CREATE INDEX IF NOT EXISTS idx_fr_pair
                ON file_records(pair_id);

            -- source_manifest indexes
            CREATE INDEX IF NOT EXISTS idx_sm_session_path
                ON source_manifest(session_id, file_path);
        """)

    # ------------------------------------------------------------------
    # Row <-> model converters
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> Session:
        return Session.from_dict(dict(row))

    @staticmethod
    def _row_to_file_record(row: sqlite3.Row) -> FileRecord:
        return FileRecord.from_dict(dict(row))

    @staticmethod
    def _row_to_duplicate_group(row: sqlite3.Row) -> DuplicateGroup:
        return DuplicateGroup.from_dict(dict(row))

    @staticmethod
    def _row_to_manifest_entry(row: sqlite3.Row) -> SourceManifestEntry:
        return SourceManifestEntry.from_dict(dict(row))

    # ------------------------------------------------------------------
    # Session CRUD
    # ------------------------------------------------------------------

    def create_session(self, session: Session) -> Session:
        with self._transaction() as cur:
            cur.execute(
                """INSERT INTO sessions
                   (id, state, created_at, updated_at, source_dirs,
                    destination_dir, config_snapshot, stats, is_archived)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session.id,
                    session.state.value,
                    session.created_at.isoformat(),
                    session.updated_at.isoformat(),
                    json.dumps(session.source_dirs),
                    session.destination_dir,
                    json.dumps(session.config_snapshot),
                    json.dumps(session.stats),
                    int(session.is_archived),
                ),
            )
        return session

    def get_session(self, session_id: str) -> Session | None:
        row = self._execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return self._row_to_session(row) if row else None

    def update_session(self, session: Session) -> None:
        with self._transaction() as cur:
            cur.execute(
                """UPDATE sessions SET
                    state = ?, updated_at = ?, source_dirs = ?,
                    destination_dir = ?, config_snapshot = ?,
                    stats = ?, is_archived = ?
                   WHERE id = ?""",
                (
                    session.state.value,
                    session.updated_at.isoformat(),
                    json.dumps(session.source_dirs),
                    session.destination_dir,
                    json.dumps(session.config_snapshot),
                    json.dumps(session.stats),
                    int(session.is_archived),
                    session.id,
                ),
            )

    def list_sessions(self, include_archived: bool = False) -> list[Session]:
        if include_archived:
            rows = self._execute(
                "SELECT * FROM sessions ORDER BY created_at DESC"
            ).fetchall()
        else:
            rows = self._execute(
                "SELECT * FROM sessions WHERE is_archived = 0 ORDER BY created_at DESC"
            ).fetchall()
        return [self._row_to_session(r) for r in rows]

    def archive_session(self, session_id: str) -> None:
        with self._transaction() as cur:
            cur.execute(
                "UPDATE sessions SET is_archived = 1 WHERE id = ?",
                (session_id,),
            )

    # ------------------------------------------------------------------
    # FileRecord CRUD
    # ------------------------------------------------------------------

    _FILE_RECORD_INSERT = """
        INSERT INTO file_records
        (id, session_id, source_path, source_dir, destination_path,
         file_type, content_type, category, file_size, sha256_hash,
         perceptual_hash, pipeline_stage, status, skip_reason, error_message,
         date_value, date_source, timezone_offset, exif_status, exif_data,
         is_duplicate, duplicate_group_id, pair_id, pair_policy, verified,
         created_at)
        VALUES (?,?,?,?,?, ?,?,?,?,?, ?,?,?,?,?, ?,?,?,?,?, ?,?,?,?,?, ?)
    """

    def _file_record_params(self, r: FileRecord) -> tuple:
        return (
            r.id, r.session_id, r.source_path, r.source_dir,
            r.destination_path, r.file_type.value, r.content_type,
            r.category, r.file_size, r.sha256_hash, r.perceptual_hash,
            r.pipeline_stage, r.status.value, r.skip_reason,
            r.error_message,
            r.date_value.isoformat() if r.date_value else None,
            r.date_source.value, r.timezone_offset, r.exif_status.value,
            json.dumps(r.exif_data) if r.exif_data is not None else None,
            int(r.is_duplicate), r.duplicate_group_id, r.pair_id,
            r.pair_policy.value if r.pair_policy else None,
            int(r.verified), r.created_at.isoformat(),
        )

    def create_file_record(self, record: FileRecord) -> FileRecord:
        with self._transaction() as cur:
            cur.execute(self._FILE_RECORD_INSERT, self._file_record_params(record))
        return record

    def create_file_records_batch(self, records: list[FileRecord]) -> None:
        params = [self._file_record_params(r) for r in records]
        with self._transaction() as cur:
            cur.executemany(self._FILE_RECORD_INSERT, params)

    def update_file_record(self, record: FileRecord) -> None:
        with self._transaction() as cur:
            cur.execute(
                """UPDATE file_records SET
                    session_id=?, source_path=?, source_dir=?,
                    destination_path=?, file_type=?, content_type=?,
                    category=?, file_size=?, sha256_hash=?,
                    perceptual_hash=?, pipeline_stage=?, status=?,
                    skip_reason=?, error_message=?, date_value=?,
                    date_source=?, timezone_offset=?, exif_status=?,
                    exif_data=?, is_duplicate=?, duplicate_group_id=?,
                    pair_id=?, pair_policy=?, verified=?, created_at=?
                   WHERE id=?""",
                (
                    record.session_id, record.source_path, record.source_dir,
                    record.destination_path, record.file_type.value,
                    record.content_type, record.category, record.file_size,
                    record.sha256_hash, record.perceptual_hash,
                    record.pipeline_stage, record.status.value,
                    record.skip_reason, record.error_message,
                    record.date_value.isoformat() if record.date_value else None,
                    record.date_source.value, record.timezone_offset,
                    record.exif_status.value,
                    json.dumps(record.exif_data) if record.exif_data is not None else None,
                    int(record.is_duplicate), record.duplicate_group_id,
                    record.pair_id,
                    record.pair_policy.value if record.pair_policy else None,
                    int(record.verified), record.created_at.isoformat(),
                    record.id,
                ),
            )

    def update_file_stage(self, file_id: str, stage: int, status: FileStatus) -> None:
        with self._transaction() as cur:
            cur.execute(
                "UPDATE file_records SET pipeline_stage = ?, status = ? WHERE id = ?",
                (stage, status.value, file_id),
            )

    def get_file_records(
        self, session_id: str, status: FileStatus | None = None
    ) -> list[FileRecord]:
        if status is not None:
            rows = self._execute(
                "SELECT * FROM file_records WHERE session_id = ? AND status = ?",
                (session_id, status.value),
            ).fetchall()
        else:
            rows = self._execute(
                "SELECT * FROM file_records WHERE session_id = ?",
                (session_id,),
            ).fetchall()
        return [self._row_to_file_record(r) for r in rows]

    def get_pending_files(self, session_id: str) -> list[FileRecord]:
        rows = self._execute(
            "SELECT * FROM file_records WHERE session_id = ? AND status IN (?, ?)",
            (session_id, FileStatus.PENDING.value, FileStatus.PROCESSING.value),
        ).fetchall()
        return [self._row_to_file_record(r) for r in rows]

    def get_file_by_hash(self, session_id: str, sha256: str) -> FileRecord | None:
        row = self._execute(
            "SELECT * FROM file_records WHERE session_id = ? AND sha256_hash = ? LIMIT 1",
            (session_id, sha256),
        ).fetchone()
        return self._row_to_file_record(row) if row else None

    # ------------------------------------------------------------------
    # DuplicateGroup CRUD
    # ------------------------------------------------------------------

    def create_duplicate_group(self, group: DuplicateGroup) -> DuplicateGroup:
        with self._transaction() as cur:
            cur.execute(
                """INSERT INTO duplicate_groups
                   (id, session_id, winner_file_id, hash_value,
                    match_type, file_count, bytes_saved)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    group.id, group.session_id, group.winner_file_id,
                    group.hash_value, group.match_type.value,
                    group.file_count, group.bytes_saved,
                ),
            )
        return group

    def get_duplicate_groups(self, session_id: str) -> list[DuplicateGroup]:
        rows = self._execute(
            "SELECT * FROM duplicate_groups WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        return [self._row_to_duplicate_group(r) for r in rows]

    # ------------------------------------------------------------------
    # Source manifest
    # ------------------------------------------------------------------

    def save_manifest(self, entries: list[SourceManifestEntry]) -> None:
        params = [
            (e.session_id, e.source_dir, e.file_path, e.file_size, e.mtime)
            for e in entries
        ]
        with self._transaction() as cur:
            cur.executemany(
                """INSERT INTO source_manifest
                   (session_id, source_dir, file_path, file_size, mtime)
                   VALUES (?, ?, ?, ?, ?)""",
                params,
            )

    def get_manifest(self, session_id: str) -> list[SourceManifestEntry]:
        rows = self._execute(
            "SELECT * FROM source_manifest WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        return [self._row_to_manifest_entry(r) for r in rows]

    def get_manifest_entry(
        self, session_id: str, file_path: str
    ) -> SourceManifestEntry | None:
        row = self._execute(
            "SELECT * FROM source_manifest WHERE session_id = ? AND file_path = ? LIMIT 1",
            (session_id, file_path),
        ).fetchone()
        return self._row_to_manifest_entry(row) if row else None

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def vacuum(self) -> None:
        with self._lock:
            self._conn.execute("VACUUM")

    def close(self) -> None:
        self._conn.close()
