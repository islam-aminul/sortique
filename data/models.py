"""Sortique data models."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sortique.constants import (
    DateSource,
    DupMatchType,
    ExifStatus,
    FileStatus,
    FileType,
    PairPolicy,
    SessionState,
)


def _new_id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _default_stats() -> dict:
    return {
        "files_processed": 0,
        "files_skipped": 0,
        "dupes_found": 0,
        "space_saved": 0,
        "duration_seconds": 0.0,
    }


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

@dataclass
class Session:
    id: str = field(default_factory=_new_id)
    state: SessionState = SessionState.PENDING
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)
    source_dirs: list[str] = field(default_factory=list)
    destination_dir: str = ""
    config_snapshot: dict = field(default_factory=dict)
    stats: dict = field(default_factory=_default_stats)
    is_archived: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "state": self.state.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "source_dirs": self.source_dirs,
            "destination_dir": self.destination_dir,
            "config_snapshot": self.config_snapshot,
            "stats": self.stats,
            "is_archived": self.is_archived,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Session:
        return cls(
            id=data["id"],
            state=SessionState(data["state"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            source_dirs=data["source_dirs"] if isinstance(data["source_dirs"], list) else json.loads(data["source_dirs"]),
            destination_dir=data["destination_dir"],
            config_snapshot=data["config_snapshot"] if isinstance(data["config_snapshot"], dict) else json.loads(data["config_snapshot"]),
            stats=data["stats"] if isinstance(data["stats"], dict) else json.loads(data["stats"]),
            is_archived=bool(data["is_archived"]),
        )


# ---------------------------------------------------------------------------
# FileRecord
# ---------------------------------------------------------------------------

@dataclass
class FileRecord:
    id: str = field(default_factory=_new_id)
    session_id: str = ""
    source_path: str = ""
    source_dir: str = ""
    destination_path: str | None = None
    file_type: FileType = FileType.UNKNOWN
    content_type: str = ""
    category: str = ""
    file_size: int = 0
    sha256_hash: str | None = None
    perceptual_hash: str | None = None
    pipeline_stage: int = 1
    status: FileStatus = FileStatus.PENDING
    skip_reason: str | None = None
    error_message: str | None = None
    date_value: datetime | None = None
    date_source: DateSource = DateSource.NONE
    timezone_offset: str | None = None
    exif_status: ExifStatus = ExifStatus.NONE
    exif_data: dict | None = None
    is_duplicate: bool = False
    duplicate_group_id: str | None = None
    pair_id: str | None = None
    pair_policy: PairPolicy | None = None
    verified: bool = False
    created_at: datetime = field(default_factory=_now)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "source_path": self.source_path,
            "source_dir": self.source_dir,
            "destination_path": self.destination_path,
            "file_type": self.file_type.value,
            "content_type": self.content_type,
            "category": self.category,
            "file_size": self.file_size,
            "sha256_hash": self.sha256_hash,
            "perceptual_hash": self.perceptual_hash,
            "pipeline_stage": self.pipeline_stage,
            "status": self.status.value,
            "skip_reason": self.skip_reason,
            "error_message": self.error_message,
            "date_value": self.date_value.isoformat() if self.date_value else None,
            "date_source": self.date_source.value,
            "timezone_offset": self.timezone_offset,
            "exif_status": self.exif_status.value,
            "exif_data": self.exif_data,
            "is_duplicate": self.is_duplicate,
            "duplicate_group_id": self.duplicate_group_id,
            "pair_id": self.pair_id,
            "pair_policy": self.pair_policy.value if self.pair_policy else None,
            "verified": self.verified,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> FileRecord:
        date_val = data.get("date_value")
        if isinstance(date_val, str):
            date_val = datetime.fromisoformat(date_val)

        exif = data.get("exif_data")
        if isinstance(exif, str):
            exif = json.loads(exif) if exif else None

        pair_policy_raw = data.get("pair_policy")
        pair_policy = PairPolicy(pair_policy_raw) if pair_policy_raw else None

        return cls(
            id=data["id"],
            session_id=data["session_id"],
            source_path=data["source_path"],
            source_dir=data["source_dir"],
            destination_path=data.get("destination_path"),
            file_type=FileType(data["file_type"]),
            content_type=data.get("content_type", ""),
            category=data.get("category", ""),
            file_size=data.get("file_size", 0),
            sha256_hash=data.get("sha256_hash"),
            perceptual_hash=data.get("perceptual_hash"),
            pipeline_stage=data.get("pipeline_stage", 1),
            status=FileStatus(data["status"]),
            skip_reason=data.get("skip_reason"),
            error_message=data.get("error_message"),
            date_value=date_val,
            date_source=DateSource(data.get("date_source", "none")),
            timezone_offset=data.get("timezone_offset"),
            exif_status=ExifStatus(data.get("exif_status", "none")),
            exif_data=exif,
            is_duplicate=bool(data.get("is_duplicate", False)),
            duplicate_group_id=data.get("duplicate_group_id"),
            pair_id=data.get("pair_id"),
            pair_policy=pair_policy,
            verified=bool(data.get("verified", False)),
            created_at=datetime.fromisoformat(data["created_at"]) if isinstance(data.get("created_at"), str) else data.get("created_at", _now()),
        )


# ---------------------------------------------------------------------------
# DuplicateGroup
# ---------------------------------------------------------------------------

@dataclass
class DuplicateGroup:
    id: str = field(default_factory=_new_id)
    session_id: str = ""
    winner_file_id: str = ""
    hash_value: str = ""
    match_type: DupMatchType = DupMatchType.EXACT
    file_count: int = 0
    bytes_saved: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "winner_file_id": self.winner_file_id,
            "hash_value": self.hash_value,
            "match_type": self.match_type.value,
            "file_count": self.file_count,
            "bytes_saved": self.bytes_saved,
        }

    @classmethod
    def from_dict(cls, data: dict) -> DuplicateGroup:
        return cls(
            id=data["id"],
            session_id=data["session_id"],
            winner_file_id=data["winner_file_id"],
            hash_value=data["hash_value"],
            match_type=DupMatchType(data["match_type"]),
            file_count=data.get("file_count", 0),
            bytes_saved=data.get("bytes_saved", 0),
        )


# ---------------------------------------------------------------------------
# SourceManifestEntry
# ---------------------------------------------------------------------------

@dataclass
class SourceManifestEntry:
    id: int = 0
    session_id: str = ""
    source_dir: str = ""
    file_path: str = ""
    file_size: int = 0
    mtime: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "source_dir": self.source_dir,
            "file_path": self.file_path,
            "file_size": self.file_size,
            "mtime": self.mtime,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SourceManifestEntry:
        return cls(
            id=data.get("id", 0),
            session_id=data["session_id"],
            source_dir=data["source_dir"],
            file_path=data["file_path"],
            file_size=data.get("file_size", 0),
            mtime=data.get("mtime", 0.0),
        )
