"""Two-tier deduplication: exact byte matching + perceptual hashing."""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sortique.constants import DupMatchType, FileType, IMAGE_EXTENSIONS
from sortique.data.models import DuplicateGroup, FileRecord

if TYPE_CHECKING:
    from sortique.data.database import Database
    from sortique.data.hash_manifest import HashManifest
    from sortique.engine.hasher import FileHasher

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class DedupResult:
    """Outcome of :meth:`DedupEngine.check_duplicate`."""

    is_duplicate: bool
    original_file_id: str | None
    duplicate_group_id: str | None
    bytes_saved: int


@dataclass
class PerceptualMatch:
    """A single perceptual-hash match pair from :meth:`DedupEngine.run_perceptual_pass`."""

    file_a_id: str
    file_b_id: str
    similarity: float
    file_a_path: str
    file_b_path: str


# ---------------------------------------------------------------------------
# DedupEngine
# ---------------------------------------------------------------------------

class DedupEngine:
    """Two-tier deduplication: exact byte matching (default) + perceptual matching (opt-in).

    **Tier 1 – exact**:  SHA-256 hash comparison during the main pipeline.
    **Tier 2 – perceptual**:  ``imagehash.phash`` comparison, run as a
    separate opt-in post-processing pass.
    """

    def __init__(self, db: Database, hasher: FileHasher) -> None:
        self.db = db
        self.hasher = hasher
        # In-memory SHA-256 → FileRecord map for the current session.
        # This is the primary lookup for dedup checks — it works in both
        # dry-run mode (where DB writes are skipped) and multi-threaded
        # mode (where DB writes happen later in the pipeline).
        self._hash_map: dict[str, FileRecord] = {}
        self._lock = threading.Lock()

        # Portable hash manifest for cross-machine dedup.
        self._manifest: HashManifest | None = None
        self._manifest_hashes: dict[str, str] = {}  # sha256 → rel_path

    # ------------------------------------------------------------------
    # Portable manifest (cross-machine dedup)
    # ------------------------------------------------------------------

    def load_manifest(self, manifest: HashManifest) -> None:
        """Load a portable hash manifest for cross-machine dedup.

        Pre-populates ``_manifest_hashes`` so that files already
        organised to the destination (possibly by a different machine
        or user) are recognised as duplicates.

        Safe to call multiple times — each call replaces the previous
        manifest.  Never raises.
        """
        try:
            # Clear stale in-memory state from a previous session /
            # dry-run so that ghost records don't cause false positives.
            self._hash_map.clear()

            self._manifest = manifest
            self._manifest_hashes = manifest.load_all()
            logger.info(
                "Loaded portable manifest with %d entries",
                len(self._manifest_hashes),
            )
        except Exception:
            logger.warning(
                "Failed to load portable hash manifest", exc_info=True,
            )
            self._manifest = None
            self._manifest_hashes = {}

    def record_in_manifest(
        self, sha256: str, rel_path: str, file_size: int,
    ) -> None:
        """Write a successfully-organised file to the portable manifest.

        Should be called after the file has been copied / moved to the
        destination.  Never raises.
        """
        if self._manifest is None:
            return
        try:
            self._manifest.add(sha256, rel_path, file_size)
        except Exception:
            logger.warning(
                "Failed to record hash in portable manifest", exc_info=True,
            )

    # ------------------------------------------------------------------
    # Tier 1: exact byte matching
    # ------------------------------------------------------------------

    def check_duplicate(
        self,
        file_record: FileRecord,
        session_id: str,
    ) -> DedupResult:
        """Check if *file_record*'s SHA-256 hash matches any existing record.

        Uses an **in-memory hash map** as the primary lookup so that
        dedup works correctly in both dry-run mode (where DB writes are
        skipped) and multi-threaded mode (where DB writes happen later
        in the pipeline).  Falls back to the database for resume
        scenarios where files were processed in a previous run.

        When a match is found the **conflict ranking** rules decide who
        is the *winner* (original) and who is the *loser* (duplicate):

        1. Shortest ``source_path`` length.
        2. Earliest file ``mtime``.
        3. Lexicographic sort of ``source_path``.

        If the incoming file *wins*, the previously-stored record is
        retroactively marked as the duplicate and the new file becomes the
        original.

        Returns a :class:`DedupResult` describing whether *file_record*
        should be treated as a duplicate.
        """
        sha = file_record.sha256_hash
        if sha is None:
            return DedupResult(
                is_duplicate=False,
                original_file_id=None,
                duplicate_group_id=None,
                bytes_saved=0,
            )

        with self._lock:
            return self._check_duplicate_locked(file_record, session_id, sha)

    def _check_duplicate_locked(
        self,
        file_record: FileRecord,
        session_id: str,
        sha: str,
    ) -> DedupResult:
        """Core dedup logic, called while holding ``self._lock``."""
        # --- lookup: in-memory map first, then DB (for resume) ---
        existing = self._hash_map.get(sha)
        if existing is None:
            existing = self.db.get_file_by_hash(session_id, sha)

        # --- no match in current session → check portable manifest ---
        if existing is None or existing.id == file_record.id:
            if sha in self._manifest_hashes:
                # File with this hash was already organised to the
                # destination (possibly by a different machine / user).
                return DedupResult(
                    is_duplicate=True,
                    original_file_id=None,
                    duplicate_group_id=None,
                    bytes_saved=file_record.file_size or 0,
                )

            self._hash_map[sha] = file_record
            return DedupResult(
                is_duplicate=False,
                original_file_id=None,
                duplicate_group_id=None,
                bytes_saved=0,
            )

        # --- match found → decide winner / loser ---
        winner, loser = self._rank_conflict(existing, file_record)

        # Determine or reuse the duplicate group
        group_id = existing.duplicate_group_id

        if group_id is not None:
            # Group already exists — just increment.
            groups = self.db.get_duplicate_groups(session_id)
            group = next((g for g in groups if g.id == group_id), None)
            if group is not None:
                group.file_count += 1
                group.bytes_saved += loser.file_size
                group.winner_file_id = winner.id
                self._update_duplicate_group(group)
        else:
            # Create a brand-new duplicate group.
            group = DuplicateGroup(
                session_id=session_id,
                winner_file_id=winner.id,
                hash_value=sha,
                match_type=DupMatchType.EXACT,
                file_count=2,
                bytes_saved=loser.file_size,
            )
            self.db.create_duplicate_group(group)
            group_id = group.id

            # Tag the winner so future lookups find the existing group.
            winner_rec = (
                existing if winner.id == existing.id else file_record
            )
            winner_rec.duplicate_group_id = group_id
            self.db.update_file_record(winner_rec)

        # --- apply the swap if the NEW file won ---
        if winner.id == file_record.id:
            # The existing record becomes the loser (duplicate).
            existing.is_duplicate = True
            existing.duplicate_group_id = group_id
            self.db.update_file_record(existing)

            # The new file is the winner (not a duplicate).
            file_record.is_duplicate = False
            file_record.duplicate_group_id = group_id
            self.db.update_file_record(file_record)

            # Update the in-memory map to point to the new winner.
            self._hash_map[sha] = file_record

            return DedupResult(
                is_duplicate=False,
                original_file_id=file_record.id,
                duplicate_group_id=group_id,
                bytes_saved=0,
            )

        # --- the existing record keeps its winner status ---
        file_record.is_duplicate = True
        file_record.duplicate_group_id = group_id
        self.db.update_file_record(file_record)

        return DedupResult(
            is_duplicate=True,
            original_file_id=winner.id,
            duplicate_group_id=group_id,
            bytes_saved=file_record.file_size,
        )

    # ------------------------------------------------------------------
    # Tier 2: perceptual hashing (opt-in)
    # ------------------------------------------------------------------

    def run_perceptual_pass(
        self,
        session_id: str,
        threshold: float = 0.95,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[PerceptualMatch]:
        """Run perceptual hashing on all non-duplicate images in the session.

        * Loads every image :class:`FileRecord` that is **not** already
          marked as a duplicate.
        * Computes a perceptual hash (``imagehash.phash``) for each.
        * Compares all pairs and returns those above *threshold*.
        * *progress_callback(files_processed, total_files)* is called
          after each file is hashed.
        """
        all_records = self.db.get_file_records(session_id)
        image_records = [
            r for r in all_records
            if r.file_type == FileType.IMAGE
            and not r.is_duplicate
            and r.source_path
        ]

        total = len(image_records)
        hashed: list[tuple[FileRecord, str]] = []

        for idx, rec in enumerate(image_records, 1):
            phash = self._compute_perceptual_hash(rec.source_path)
            if phash is not None:
                rec.perceptual_hash = phash
                self.db.update_file_record(rec)
                hashed.append((rec, phash))
            if progress_callback is not None:
                progress_callback(idx, total)

        # --- pairwise comparison ---
        matches: list[PerceptualMatch] = []
        n = len(hashed)
        for i in range(n):
            for j in range(i + 1, n):
                sim = self._compare_perceptual(hashed[i][1], hashed[j][1])
                if sim >= threshold:
                    rec_a, rec_b = hashed[i][0], hashed[j][0]
                    matches.append(PerceptualMatch(
                        file_a_id=rec_a.id,
                        file_b_id=rec_b.id,
                        similarity=sim,
                        file_a_path=rec_a.source_path,
                        file_b_path=rec_b.source_path,
                    ))

        return matches

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_perceptual_hash(self, filepath: str) -> str | None:
        """Compute perceptual hash using ``imagehash.phash()``.

        Returns the hex string representation, or ``None`` when the
        image cannot be opened (corrupt file, non-image, etc.).
        """
        try:
            import imagehash
            from PIL import Image

            with Image.open(filepath) as img:
                return str(imagehash.phash(img))
        except Exception:
            return None

    def _compare_perceptual(self, hash1: str, hash2: str) -> float:
        """Compare two perceptual hashes.

        Returns a similarity score in ``[0.0, 1.0]``.

        ``similarity = 1 - (hamming_distance / hash_bits)``
        """
        import imagehash

        h1 = imagehash.hex_to_hash(hash1)
        h2 = imagehash.hex_to_hash(hash2)
        distance = h1 - h2  # Hamming distance
        hash_bits = h1.hash.size
        if hash_bits == 0:
            return 1.0
        return 1.0 - (distance / hash_bits)

    def _rank_conflict(
        self,
        file_a: FileRecord,
        file_b: FileRecord,
    ) -> tuple[FileRecord, FileRecord]:
        """Apply conflict ranking rules.  Returns ``(winner, loser)``.

        Rules (applied in order):

        1. **Shorter** ``source_path`` wins.
        2. Earlier file ``mtime`` (from ``os.path.getmtime``) wins.
        3. Lexicographically smaller ``source_path`` wins.
        """
        len_a = len(file_a.source_path)
        len_b = len(file_b.source_path)

        if len_a != len_b:
            return (file_a, file_b) if len_a < len_b else (file_b, file_a)

        # --- tie-break on mtime ---
        mtime_a = _safe_mtime(file_a.source_path)
        mtime_b = _safe_mtime(file_b.source_path)

        if mtime_a != mtime_b:
            return (file_a, file_b) if mtime_a < mtime_b else (file_b, file_a)

        # --- final tie-break: lexicographic ---
        if file_a.source_path <= file_b.source_path:
            return (file_a, file_b)
        return (file_b, file_a)

    # ------------------------------------------------------------------
    # DB helper (update duplicate_group in-place)
    # ------------------------------------------------------------------

    def _update_duplicate_group(self, group: DuplicateGroup) -> None:
        """Persist updated fields of an existing :class:`DuplicateGroup`."""
        with self.db._transaction() as cur:
            cur.execute(
                """UPDATE duplicate_groups
                   SET winner_file_id = ?, file_count = ?, bytes_saved = ?
                   WHERE id = ?""",
                (group.winner_file_id, group.file_count, group.bytes_saved,
                 group.id),
            )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _safe_mtime(filepath: str) -> float:
    """Return the file's mtime, or ``inf`` if the file is inaccessible."""
    try:
        return os.path.getmtime(filepath)
    except OSError:
        return float("inf")
