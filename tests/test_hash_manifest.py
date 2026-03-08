"""Tests for sortique.data.hash_manifest."""

from __future__ import annotations

import threading

import pytest

from sortique.data.hash_manifest import HashManifest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def manifest(tmp_path):
    """A fresh HashManifest rooted at a temporary directory."""
    return HashManifest(str(tmp_path))


# ===========================================================================
# 1. Basic CRUD
# ===========================================================================

class TestHashManifestCRUD:
    """Create, read, update, delete operations."""

    def test_create_manifest_db(self, tmp_path):
        """Creating a HashManifest creates the .sortique dir and DB file."""
        dest = str(tmp_path / "destination")
        m = HashManifest(dest)

        assert (tmp_path / "destination" / ".sortique" / "hash_manifest.db").exists()
        m.close()

    def test_load_empty_manifest(self, manifest):
        """A fresh manifest returns an empty dict."""
        assert manifest.load_all() == {}

    def test_add_and_load(self, manifest):
        """Round-trip: add an entry, then load_all returns it."""
        manifest.add("abc123", "Images/photo.jpg", 5000)

        result = manifest.load_all()
        assert result == {"abc123": "Images/photo.jpg"}

    def test_add_multiple_entries(self, manifest):
        """Multiple distinct hashes are all retrievable."""
        manifest.add("hash-1", "Images/a.jpg", 1000)
        manifest.add("hash-2", "Videos/b.mp4", 2000)
        manifest.add("hash-3", "Audio/c.mp3", 3000)

        result = manifest.load_all()
        assert len(result) == 3
        assert result["hash-1"] == "Images/a.jpg"
        assert result["hash-2"] == "Videos/b.mp4"
        assert result["hash-3"] == "Audio/c.mp3"

    def test_add_replaces_existing(self, manifest):
        """INSERT OR REPLACE: same hash updates the rel_path."""
        manifest.add("abc123", "Images/old.jpg", 1000)
        manifest.add("abc123", "Images/new.jpg", 2000)

        result = manifest.load_all()
        assert result == {"abc123": "Images/new.jpg"}

    def test_remove_entry(self, manifest):
        """Remove deletes a hash entry."""
        manifest.add("abc123", "Images/photo.jpg", 5000)
        manifest.add("def456", "Videos/clip.mp4", 8000)

        manifest.remove("abc123")

        result = manifest.load_all()
        assert result == {"def456": "Videos/clip.mp4"}

    def test_remove_nonexistent_no_error(self, manifest):
        """Removing a hash that doesn't exist doesn't raise."""
        manifest.remove("not-there")  # Should not raise
        assert manifest.load_all() == {}


# ===========================================================================
# 2. Class helpers
# ===========================================================================

class TestHashManifestHelpers:

    def test_exists_true(self, tmp_path):
        """exists() returns True when the manifest DB file is present."""
        m = HashManifest(str(tmp_path))
        m.close()

        assert HashManifest.exists(str(tmp_path)) is True

    def test_exists_false(self, tmp_path):
        """exists() returns False when no manifest has been created."""
        assert HashManifest.exists(str(tmp_path / "empty")) is False


# ===========================================================================
# 3. Thread safety
# ===========================================================================

class TestHashManifestThreadSafety:

    def test_concurrent_writes(self, manifest):
        """Multiple threads writing concurrently don't corrupt the DB."""
        num_threads = 20
        errors: list[Exception] = []

        def worker(idx: int) -> None:
            try:
                manifest.add(f"hash-{idx}", f"Images/photo-{idx}.jpg", idx * 100)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(i,))
            for i in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []

        result = manifest.load_all()
        assert len(result) == num_threads
        for i in range(num_threads):
            assert f"hash-{i}" in result

    def test_concurrent_reads_and_writes(self, manifest):
        """Reading while writing doesn't crash."""
        # Pre-populate some entries.
        for i in range(10):
            manifest.add(f"pre-{i}", f"Images/pre-{i}.jpg", i * 100)

        errors: list[Exception] = []

        def writer(idx: int) -> None:
            try:
                manifest.add(f"new-{idx}", f"Images/new-{idx}.jpg", idx * 100)
            except Exception as exc:
                errors.append(exc)

        def reader() -> None:
            try:
                manifest.load_all()
            except Exception as exc:
                errors.append(exc)

        threads = []
        for i in range(10):
            threads.append(threading.Thread(target=writer, args=(i,)))
            threads.append(threading.Thread(target=reader))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []


# ===========================================================================
# 4. Persistence across instances
# ===========================================================================

class TestHashManifestPersistence:

    def test_data_persists_across_instances(self, tmp_path):
        """Data written by one instance is readable by another."""
        dest = str(tmp_path)

        # First instance writes data.
        m1 = HashManifest(dest)
        m1.add("hash-a", "Images/a.jpg", 1000)
        m1.add("hash-b", "Videos/b.mp4", 2000)
        m1.close()

        # Second instance reads data (simulates different machine / session).
        m2 = HashManifest(dest)
        result = m2.load_all()
        m2.close()

        assert result == {
            "hash-a": "Images/a.jpg",
            "hash-b": "Videos/b.mp4",
        }
