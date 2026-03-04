"""Tests for sortique.engine.dedup."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import pytest

from sortique.constants import DupMatchType, FileStatus, FileType, SessionState
from sortique.data.database import Database
from sortique.data.models import DuplicateGroup, FileRecord, Session
from sortique.engine.dedup import DedupEngine, DedupResult, PerceptualMatch
from sortique.engine.hasher import FileHasher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _session(sid: str = "sess-1") -> Session:
    return Session(
        id=sid,
        state=SessionState.IN_PROGRESS,
        source_dirs=["/src"],
        destination_dir="/dst",
    )


def _record(
    *,
    rid: str | None = None,
    session_id: str = "sess-1",
    source_path: str = "/src/a.jpg",
    sha256: str | None = "abc123",
    file_size: int = 1000,
    file_type: FileType = FileType.IMAGE,
) -> FileRecord:
    return FileRecord(
        id=rid or f"file-{source_path}",
        session_id=session_id,
        source_path=source_path,
        source_dir="/src",
        file_type=file_type,
        file_size=file_size,
        sha256_hash=sha256,
        status=FileStatus.PROCESSING,
    )


@pytest.fixture()
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.create_session(_session())
    return d


@pytest.fixture()
def hasher():
    return FileHasher()


@pytest.fixture()
def engine(db, hasher):
    return DedupEngine(db, hasher)


# ===========================================================================
# 1.  Exact duplicate detection
# ===========================================================================

class TestExactDuplicate:
    """Tier 1: SHA-256 byte-exact matching."""

    def test_duplicate_detected(self, engine, db):
        """Second file with same hash is flagged as duplicate."""
        original = _record(rid="file-orig", source_path="/src/photo.jpg")
        db.create_file_record(original)

        dupe = _record(rid="file-dupe", source_path="/src/backup/photo.jpg")
        result = engine.check_duplicate(dupe, "sess-1")

        assert result.is_duplicate is True
        assert result.original_file_id == "file-orig"
        assert result.duplicate_group_id is not None
        assert result.bytes_saved == 1000

    def test_non_duplicate_different_hash(self, engine, db):
        """Files with different hashes are not duplicates."""
        a = _record(rid="file-a", source_path="/src/a.jpg", sha256="hash-a")
        db.create_file_record(a)

        b = _record(rid="file-b", source_path="/src/b.jpg", sha256="hash-b")
        result = engine.check_duplicate(b, "sess-1")

        assert result.is_duplicate is False
        assert result.original_file_id is None
        assert result.duplicate_group_id is None
        assert result.bytes_saved == 0

    def test_no_hash_not_duplicate(self, engine, db):
        """File with no SHA-256 hash is never a duplicate."""
        rec = _record(rid="file-nohash", sha256=None)
        result = engine.check_duplicate(rec, "sess-1")

        assert result.is_duplicate is False
        assert result.bytes_saved == 0

    def test_first_file_not_duplicate(self, engine, db):
        """Very first file with a given hash is not a duplicate."""
        rec = _record(rid="file-first", sha256="unique-hash")
        result = engine.check_duplicate(rec, "sess-1")

        assert result.is_duplicate is False
        assert result.original_file_id is None

    def test_duplicate_group_created_in_db(self, engine, db):
        """A DuplicateGroup is persisted when a duplicate pair is found."""
        original = _record(rid="file-orig", source_path="/src/a.jpg")
        db.create_file_record(original)

        dupe = _record(rid="file-dupe", source_path="/src/backup/a.jpg")
        result = engine.check_duplicate(dupe, "sess-1")

        groups = db.get_duplicate_groups("sess-1")
        assert len(groups) == 1
        g = groups[0]
        assert g.id == result.duplicate_group_id
        assert g.match_type == DupMatchType.EXACT
        assert g.file_count == 2
        assert g.winner_file_id == "file-orig"

    def test_third_duplicate_increments_group(self, engine, db):
        """A third file with same hash increments the existing group."""
        original = _record(rid="file-orig", source_path="/src/a.jpg")
        db.create_file_record(original)

        dupe1 = _record(rid="file-d1", source_path="/src/backup1/a.jpg")
        engine.check_duplicate(dupe1, "sess-1")

        dupe2 = _record(rid="file-d2", source_path="/src/backup2/a.jpg")
        result = engine.check_duplicate(dupe2, "sess-1")

        groups = db.get_duplicate_groups("sess-1")
        assert len(groups) == 1
        assert groups[0].file_count == 3

    def test_file_record_marked_duplicate_in_db(self, engine, db):
        """The loser FileRecord is updated in the database."""
        original = _record(rid="file-orig", source_path="/src/a.jpg")
        db.create_file_record(original)

        dupe = _record(rid="file-dupe", source_path="/src/backup/a.jpg")
        db.create_file_record(dupe)
        engine.check_duplicate(dupe, "sess-1")

        updated = db.get_file_records("sess-1")
        dupe_rec = next(r for r in updated if r.id == "file-dupe")
        assert dupe_rec.is_duplicate is True
        assert dupe_rec.duplicate_group_id is not None


# ===========================================================================
# 2.  Conflict ranking
# ===========================================================================

class TestConflictRanking:
    """The _rank_conflict method decides winner vs loser."""

    def test_shorter_path_wins(self, engine):
        """File with shorter source_path is the winner."""
        short = _record(rid="short", source_path="/a/b.jpg")
        long = _record(rid="long", source_path="/a/very/deep/b.jpg")

        winner, loser = engine._rank_conflict(short, long)
        assert winner.id == "short"
        assert loser.id == "long"

    def test_shorter_path_wins_reversed(self, engine):
        """Order of arguments doesn't matter — shorter path still wins."""
        short = _record(rid="short", source_path="/a/b.jpg")
        long = _record(rid="long", source_path="/a/very/deep/b.jpg")

        winner, loser = engine._rank_conflict(long, short)
        assert winner.id == "short"
        assert loser.id == "long"

    def test_earlier_mtime_wins(self, engine, tmp_path):
        """When paths are equal length, earlier mtime wins."""
        # Create two real files with controlled mtimes.
        old_file = tmp_path / "aaaa_old.jpg"
        new_file = tmp_path / "aaaa_new.jpg"
        old_file.write_bytes(b"data")
        new_file.write_bytes(b"data")

        # Set mtimes: old_file gets an older timestamp.
        os.utime(old_file, (1000.0, 1000.0))
        os.utime(new_file, (2000.0, 2000.0))

        rec_old = _record(rid="old", source_path=str(old_file))
        rec_new = _record(rid="new", source_path=str(new_file))

        winner, loser = engine._rank_conflict(rec_old, rec_new)
        assert winner.id == "old"
        assert loser.id == "new"

    def test_earlier_mtime_wins_reversed(self, engine, tmp_path):
        """Argument order doesn't affect mtime ranking."""
        old_file = tmp_path / "aaaa_old.jpg"
        new_file = tmp_path / "aaaa_new.jpg"
        old_file.write_bytes(b"data")
        new_file.write_bytes(b"data")
        os.utime(old_file, (1000.0, 1000.0))
        os.utime(new_file, (2000.0, 2000.0))

        rec_old = _record(rid="old", source_path=str(old_file))
        rec_new = _record(rid="new", source_path=str(new_file))

        winner, loser = engine._rank_conflict(rec_new, rec_old)
        assert winner.id == "old"
        assert loser.id == "new"

    def test_lexicographic_tiebreaker(self, engine, tmp_path):
        """When path length and mtime are identical, lexicographic order wins."""
        file_a = tmp_path / "aaa.jpg"
        file_z = tmp_path / "zzz.jpg"
        file_a.write_bytes(b"data")
        file_z.write_bytes(b"data")
        # Force identical mtimes.
        os.utime(file_a, (5000.0, 5000.0))
        os.utime(file_z, (5000.0, 5000.0))

        rec_a = _record(rid="a", source_path=str(file_a))
        rec_z = _record(rid="z", source_path=str(file_z))

        winner, loser = engine._rank_conflict(rec_a, rec_z)
        assert winner.id == "a"
        assert loser.id == "z"

    def test_lexicographic_tiebreaker_reversed(self, engine, tmp_path):
        """Argument order doesn't affect lexicographic tiebreaker."""
        file_a = tmp_path / "aaa.jpg"
        file_z = tmp_path / "zzz.jpg"
        file_a.write_bytes(b"data")
        file_z.write_bytes(b"data")
        os.utime(file_a, (5000.0, 5000.0))
        os.utime(file_z, (5000.0, 5000.0))

        rec_a = _record(rid="a", source_path=str(file_a))
        rec_z = _record(rid="z", source_path=str(file_z))

        winner, loser = engine._rank_conflict(rec_z, rec_a)
        assert winner.id == "a"
        assert loser.id == "z"

    def test_nonexistent_file_mtime_loses(self, engine, tmp_path):
        """A file that no longer exists on disk loses the mtime comparison."""
        real = tmp_path / "real_file.jpg"
        real.write_bytes(b"data")

        rec_real = _record(rid="real", source_path=str(real))
        rec_gone = _record(rid="gone", source_path=str(tmp_path / "gone_file.jpg"))

        # Both paths have equal length — so mtime decides.  "gone" gets inf.
        winner, loser = engine._rank_conflict(rec_real, rec_gone)
        assert winner.id == "real"
        assert loser.id == "gone"


# ===========================================================================
# 3.  Winner/loser swap
# ===========================================================================

class TestWinnerLoserSwap:
    """When the new file beats the existing file, records are swapped."""

    def test_new_file_wins_swaps_records(self, engine, db):
        """If new file has shorter path, the existing record becomes the dupe."""
        existing = _record(
            rid="file-existing",
            source_path="/src/very/deep/nested/photo.jpg",
        )
        db.create_file_record(existing)

        new_file = _record(
            rid="file-new",
            source_path="/src/photo.jpg",
        )
        result = engine.check_duplicate(new_file, "sess-1")

        # The new file won — so it is NOT marked as duplicate.
        assert result.is_duplicate is False
        assert result.original_file_id == "file-new"
        assert result.bytes_saved == 0

        # The existing record should now be the duplicate.
        records = db.get_file_records("sess-1")
        existing_rec = next(r for r in records if r.id == "file-existing")
        assert existing_rec.is_duplicate is True
        assert existing_rec.duplicate_group_id == result.duplicate_group_id

    def test_existing_wins_new_is_duplicate(self, engine, db):
        """Standard case: existing has shorter path, new file is the dupe."""
        existing = _record(
            rid="file-existing",
            source_path="/src/a.jpg",
        )
        db.create_file_record(existing)

        new_file = _record(
            rid="file-new",
            source_path="/src/backup/deep/a.jpg",
        )
        result = engine.check_duplicate(new_file, "sess-1")

        assert result.is_duplicate is True
        assert result.original_file_id == "file-existing"
        assert result.bytes_saved == 1000

    def test_swap_updates_group_winner(self, engine, db):
        """After a swap the DuplicateGroup.winner_file_id points to the new winner."""
        existing = _record(
            rid="file-existing",
            source_path="/src/very/deep/nested/photo.jpg",
        )
        db.create_file_record(existing)

        new_file = _record(
            rid="file-new",
            source_path="/src/photo.jpg",
        )
        result = engine.check_duplicate(new_file, "sess-1")

        groups = db.get_duplicate_groups("sess-1")
        assert len(groups) == 1
        assert groups[0].winner_file_id == "file-new"

    def test_new_file_record_gets_group_id(self, engine, db):
        """After a swap the new winner's record has the group id set."""
        existing = _record(
            rid="file-existing",
            source_path="/src/very/deep/nested/photo.jpg",
        )
        db.create_file_record(existing)

        new_file = _record(
            rid="file-new",
            source_path="/src/photo.jpg",
        )
        db.create_file_record(new_file)
        result = engine.check_duplicate(new_file, "sess-1")

        updated = next(
            r for r in db.get_file_records("sess-1") if r.id == "file-new"
        )
        assert updated.is_duplicate is False
        assert updated.duplicate_group_id == result.duplicate_group_id


# ===========================================================================
# 4.  DedupResult dataclass
# ===========================================================================

class TestDedupResult:

    def test_fields(self):
        r = DedupResult(
            is_duplicate=True,
            original_file_id="f1",
            duplicate_group_id="g1",
            bytes_saved=42,
        )
        assert r.is_duplicate is True
        assert r.original_file_id == "f1"
        assert r.duplicate_group_id == "g1"
        assert r.bytes_saved == 42

    def test_non_duplicate(self):
        r = DedupResult(
            is_duplicate=False,
            original_file_id=None,
            duplicate_group_id=None,
            bytes_saved=0,
        )
        assert r.is_duplicate is False


# ===========================================================================
# 5.  Perceptual hashing helpers
# ===========================================================================

class TestPerceptualHelpers:

    def test_compute_perceptual_hash_valid_image(self, engine, tmp_path):
        """A valid image produces a non-None hex hash string."""
        from PIL import Image

        img = Image.new("RGB", (64, 64), color="red")
        path = tmp_path / "red.png"
        img.save(str(path))

        result = engine._compute_perceptual_hash(str(path))
        assert result is not None
        assert isinstance(result, str)
        assert len(result) > 0

    def test_compute_perceptual_hash_corrupt_file(self, engine, tmp_path):
        """A corrupt file returns None."""
        bad = tmp_path / "bad.jpg"
        bad.write_bytes(b"not-a-real-image")
        assert engine._compute_perceptual_hash(str(bad)) is None

    def test_compute_perceptual_hash_nonexistent(self, engine):
        """A nonexistent file returns None."""
        assert engine._compute_perceptual_hash("/no/such/file.jpg") is None

    def test_compare_identical_hashes(self, engine, tmp_path):
        """Identical images produce similarity of 1.0."""
        from PIL import Image

        img = Image.new("RGB", (64, 64), color="blue")
        p1 = tmp_path / "blue1.png"
        p2 = tmp_path / "blue2.png"
        img.save(str(p1))
        img.save(str(p2))

        h1 = engine._compute_perceptual_hash(str(p1))
        h2 = engine._compute_perceptual_hash(str(p2))
        assert h1 is not None and h2 is not None
        assert engine._compare_perceptual(h1, h2) == 1.0

    def test_compare_different_hashes(self, engine, tmp_path):
        """Very different images have lower similarity."""
        from PIL import Image

        white = Image.new("RGB", (64, 64), color="white")
        # Create a striped pattern for maximal visual difference
        import numpy as np
        arr = np.zeros((64, 64, 3), dtype=np.uint8)
        arr[::2, :, :] = 255  # alternating rows white
        striped = Image.fromarray(arr)

        p1 = tmp_path / "white.png"
        p2 = tmp_path / "striped.png"
        white.save(str(p1))
        striped.save(str(p2))

        h1 = engine._compute_perceptual_hash(str(p1))
        h2 = engine._compute_perceptual_hash(str(p2))
        assert h1 is not None and h2 is not None
        sim = engine._compare_perceptual(h1, h2)
        assert 0.0 <= sim <= 1.0


# ===========================================================================
# 6.  Perceptual pass (integration)
# ===========================================================================

class TestPerceptualPass:

    def test_pass_returns_matches(self, engine, db, tmp_path):
        """Identical images are reported as perceptual matches."""
        from PIL import Image

        img = Image.new("RGB", (64, 64), color="green")
        p1 = tmp_path / "green1.png"
        p2 = tmp_path / "green2.png"
        img.save(str(p1))
        img.save(str(p2))

        rec1 = _record(
            rid="img-1", source_path=str(p1), sha256="h1",
            file_type=FileType.IMAGE,
        )
        rec2 = _record(
            rid="img-2", source_path=str(p2), sha256="h2",
            file_type=FileType.IMAGE,
        )
        db.create_file_record(rec1)
        db.create_file_record(rec2)

        matches = engine.run_perceptual_pass("sess-1", threshold=0.9)
        assert len(matches) >= 1
        m = matches[0]
        assert m.similarity >= 0.9
        assert isinstance(m, PerceptualMatch)

    def test_pass_skips_duplicates(self, engine, db, tmp_path):
        """Records already marked as duplicates are excluded."""
        from PIL import Image

        img = Image.new("RGB", (64, 64), color="cyan")
        p1 = tmp_path / "c1.png"
        p2 = tmp_path / "c2.png"
        img.save(str(p1))
        img.save(str(p2))

        rec1 = _record(
            rid="img-1", source_path=str(p1), sha256="h1",
            file_type=FileType.IMAGE,
        )
        rec2 = _record(
            rid="img-2", source_path=str(p2), sha256="h2",
            file_type=FileType.IMAGE,
        )
        rec2.is_duplicate = True  # Already a duplicate

        db.create_file_record(rec1)
        db.create_file_record(rec2)

        matches = engine.run_perceptual_pass("sess-1", threshold=0.9)
        # Only one non-duplicate image → zero pairs to compare.
        assert len(matches) == 0

    def test_pass_skips_non_image_files(self, engine, db, tmp_path):
        """Non-image file types are excluded from perceptual pass."""
        from PIL import Image

        img = Image.new("RGB", (64, 64), color="red")
        p1 = tmp_path / "red.png"
        img.save(str(p1))

        rec1 = _record(
            rid="img-1", source_path=str(p1), sha256="h1",
            file_type=FileType.IMAGE,
        )
        rec2 = _record(
            rid="vid-1", source_path="/src/video.mp4", sha256="h2",
            file_type=FileType.VIDEO,
        )
        db.create_file_record(rec1)
        db.create_file_record(rec2)

        matches = engine.run_perceptual_pass("sess-1", threshold=0.9)
        # Only one image → no pairs.
        assert len(matches) == 0

    def test_progress_callback_called(self, engine, db, tmp_path):
        """Progress callback receives (files_processed, total_files)."""
        from PIL import Image

        img = Image.new("RGB", (64, 64), color="yellow")
        p1 = tmp_path / "y1.png"
        p2 = tmp_path / "y2.png"
        img.save(str(p1))
        img.save(str(p2))

        rec1 = _record(
            rid="img-1", source_path=str(p1), sha256="h1",
            file_type=FileType.IMAGE,
        )
        rec2 = _record(
            rid="img-2", source_path=str(p2), sha256="h2",
            file_type=FileType.IMAGE,
        )
        db.create_file_record(rec1)
        db.create_file_record(rec2)

        calls: list[tuple[int, int]] = []
        engine.run_perceptual_pass(
            "sess-1", threshold=0.9,
            progress_callback=lambda done, total: calls.append((done, total)),
        )
        assert len(calls) == 2
        assert calls[0] == (1, 2)
        assert calls[1] == (2, 2)

    def test_perceptual_hash_stored_in_db(self, engine, db, tmp_path):
        """After perceptual pass, FileRecords have perceptual_hash populated."""
        from PIL import Image

        img = Image.new("RGB", (64, 64), color="purple")
        p1 = tmp_path / "p1.png"
        img.save(str(p1))

        rec = _record(
            rid="img-1", source_path=str(p1), sha256="h1",
            file_type=FileType.IMAGE,
        )
        db.create_file_record(rec)

        engine.run_perceptual_pass("sess-1")

        records = db.get_file_records("sess-1")
        assert records[0].perceptual_hash is not None
        assert len(records[0].perceptual_hash) > 0


# ===========================================================================
# 7.  PerceptualMatch dataclass
# ===========================================================================

class TestPerceptualMatch:

    def test_fields(self):
        m = PerceptualMatch(
            file_a_id="a",
            file_b_id="b",
            similarity=0.97,
            file_a_path="/src/a.jpg",
            file_b_path="/src/b.jpg",
        )
        assert m.file_a_id == "a"
        assert m.file_b_id == "b"
        assert m.similarity == 0.97
        assert m.file_a_path == "/src/a.jpg"
        assert m.file_b_path == "/src/b.jpg"
