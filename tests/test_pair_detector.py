"""Tests for sortique.engine.pair_detector."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from sortique.constants import FileType
from sortique.data.models import FileRecord
from sortique.engine.pair_detector import (
    JPEG_EXTENSIONS,
    RAW_EXTENSIONS,
    FilePair,
    PairDetector,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rec(
    source_path: str,
    source_dir: str | None = None,
    file_type: FileType = FileType.IMAGE,
    rec_id: str | None = None,
) -> FileRecord:
    """Build a minimal FileRecord for pair-detection tests."""
    if source_dir is None:
        source_dir = os.path.dirname(source_path)
    rec = FileRecord(
        source_path=source_path,
        source_dir=source_dir,
        file_type=file_type,
    )
    if rec_id is not None:
        rec.id = rec_id
    return rec


# ---------------------------------------------------------------------------
# Extension sets sanity checks
# ---------------------------------------------------------------------------

class TestExtensionSets:
    def test_raw_extensions_are_lowercase_with_dot(self):
        for ext in RAW_EXTENSIONS:
            assert ext.startswith("."), f"{ext} should start with '.'"
            assert ext == ext.lower(), f"{ext} should be lower-case"

    def test_jpeg_extensions_are_lowercase_with_dot(self):
        for ext in JPEG_EXTENSIONS:
            assert ext.startswith(".")
            assert ext == ext.lower()

    def test_no_overlap_between_raw_and_jpeg(self):
        assert RAW_EXTENSIONS.isdisjoint(JPEG_EXTENSIONS)

    def test_common_raw_formats_included(self):
        for ext in (".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf", ".raf"):
            assert ext in RAW_EXTENSIONS

    def test_jpeg_formats(self):
        assert ".jpg" in JPEG_EXTENSIONS
        assert ".jpeg" in JPEG_EXTENSIONS


# ---------------------------------------------------------------------------
# detect_pairs
# ---------------------------------------------------------------------------

class TestDetectPairs:
    """Tests for PairDetector.detect_pairs."""

    def setup_method(self):
        self.detector = PairDetector()

    # -- Basic pair detection -----------------------------------------------

    def test_matching_cr2_and_jpg(self):
        """CR2 + JPG in the same dir with the same stem → one pair."""
        records = [
            _rec("/photos/IMG_1234.CR2", "/photos"),
            _rec("/photos/IMG_1234.JPG", "/photos"),
        ]
        pairs = self.detector.detect_pairs(records)

        assert len(pairs) == 1
        pair = pairs[0]
        assert pair.raw_path == "/photos/IMG_1234.CR2"
        assert pair.jpeg_path == "/photos/IMG_1234.JPG"
        assert pair.stem == "IMG_1234"

    def test_matching_nef_and_jpeg(self):
        """NEF + .jpeg extension also forms a pair."""
        records = [
            _rec("/shots/DSC_0001.nef", "/shots"),
            _rec("/shots/DSC_0001.jpeg", "/shots"),
        ]
        pairs = self.detector.detect_pairs(records)
        assert len(pairs) == 1
        assert pairs[0].stem == "DSC_0001"

    def test_case_insensitive_stem_matching(self):
        """Stems differing only in case should still pair."""
        records = [
            _rec("/pics/photo.ARW", "/pics"),
            _rec("/pics/PHOTO.jpg", "/pics"),
        ]
        pairs = self.detector.detect_pairs(records)
        assert len(pairs) == 1
        # stem comes from the RAW file's original case
        assert pairs[0].stem == "photo"

    # -- No pair scenarios --------------------------------------------------

    def test_no_pair_when_only_raw_exists(self):
        """A RAW file without a matching JPEG produces no pair."""
        records = [
            _rec("/photos/IMG_5678.CR2", "/photos"),
        ]
        pairs = self.detector.detect_pairs(records)
        assert pairs == []

    def test_no_pair_when_only_jpeg_exists(self):
        """A JPEG without a matching RAW produces no pair."""
        records = [
            _rec("/photos/IMG_5678.JPG", "/photos"),
        ]
        pairs = self.detector.detect_pairs(records)
        assert pairs == []

    def test_no_pair_when_files_in_different_directories(self):
        """RAW and JPEG in different dirs should NOT pair."""
        records = [
            _rec("/folder_a/IMG_1234.CR2", "/folder_a"),
            _rec("/folder_b/IMG_1234.JPG", "/folder_b"),
        ]
        pairs = self.detector.detect_pairs(records)
        assert pairs == []

    def test_no_pair_when_stems_differ(self):
        """RAW and JPEG with different stems should NOT pair."""
        records = [
            _rec("/photos/IMG_0001.CR2", "/photos"),
            _rec("/photos/IMG_0002.JPG", "/photos"),
        ]
        pairs = self.detector.detect_pairs(records)
        assert pairs == []

    def test_no_pair_when_two_raws_and_one_jpeg(self):
        """Ambiguous: two RAWs for one JPEG → no pair (not exactly 1 RAW)."""
        records = [
            _rec("/photos/IMG_1234.CR2", "/photos"),
            _rec("/photos/IMG_1234.DNG", "/photos"),
            _rec("/photos/IMG_1234.JPG", "/photos"),
        ]
        pairs = self.detector.detect_pairs(records)
        assert pairs == []

    def test_no_pair_when_one_raw_and_two_jpegs(self):
        """Ambiguous: one RAW for two JPEGs → no pair."""
        records = [
            _rec("/photos/IMG_1234.CR2", "/photos"),
            _rec("/photos/IMG_1234.jpg", "/photos"),
            _rec("/photos/IMG_1234.jpeg", "/photos"),
        ]
        pairs = self.detector.detect_pairs(records)
        assert pairs == []

    # -- Multiple pairs -----------------------------------------------------

    def test_multiple_pairs_in_same_directory(self):
        """Two distinct pairs in the same directory."""
        records = [
            _rec("/photos/IMG_0001.CR2", "/photos"),
            _rec("/photos/IMG_0001.JPG", "/photos"),
            _rec("/photos/IMG_0002.NEF", "/photos"),
            _rec("/photos/IMG_0002.jpg", "/photos"),
        ]
        pairs = self.detector.detect_pairs(records)
        assert len(pairs) == 2

        # Sorted by raw_path
        assert pairs[0].raw_path == "/photos/IMG_0001.CR2"
        assert pairs[1].raw_path == "/photos/IMG_0002.NEF"

    def test_pairs_across_multiple_directories(self):
        """Pairs in different directories are independent."""
        records = [
            _rec("/a/IMG.CR2", "/a"),
            _rec("/a/IMG.JPG", "/a"),
            _rec("/b/IMG.CR2", "/b"),
            _rec("/b/IMG.JPG", "/b"),
        ]
        pairs = self.detector.detect_pairs(records)
        assert len(pairs) == 2
        assert pairs[0].raw_path == "/a/IMG.CR2"
        assert pairs[1].raw_path == "/b/IMG.CR2"

    # -- Non-image files are ignored ----------------------------------------

    def test_non_image_extensions_ignored(self):
        """Files with non-RAW/non-JPEG extensions don't interfere."""
        records = [
            _rec("/photos/IMG_1234.CR2", "/photos"),
            _rec("/photos/IMG_1234.JPG", "/photos"),
            _rec("/photos/IMG_1234.xmp", "/photos"),
            _rec("/photos/IMG_1234.mp4", "/photos", file_type=FileType.VIDEO),
        ]
        pairs = self.detector.detect_pairs(records)
        assert len(pairs) == 1

    # -- Deterministic ordering ---------------------------------------------

    def test_pairs_are_sorted_by_raw_path(self):
        """Returned pairs should be sorted by raw_path for determinism."""
        records = [
            _rec("/z/IMG.ARW", "/z"),
            _rec("/z/IMG.jpg", "/z"),
            _rec("/a/IMG.CR2", "/a"),
            _rec("/a/IMG.jpg", "/a"),
        ]
        pairs = self.detector.detect_pairs(records)
        assert len(pairs) == 2
        assert pairs[0].raw_path < pairs[1].raw_path

    # -- Empty input --------------------------------------------------------

    def test_empty_input(self):
        assert self.detector.detect_pairs([]) == []


# ---------------------------------------------------------------------------
# link_pairs_in_db
# ---------------------------------------------------------------------------

class TestLinkPairsInDb:
    """Tests for PairDetector.link_pairs_in_db."""

    def setup_method(self):
        self.detector = PairDetector()

    def test_cross_links_pair_ids(self):
        """RAW.pair_id → JPEG.id and JPEG.pair_id → RAW.id."""
        raw_rec = _rec("/p/IMG.CR2", "/p", rec_id="raw-id-1")
        jpeg_rec = _rec("/p/IMG.JPG", "/p", rec_id="jpeg-id-1")

        records = {
            "/p/IMG.CR2": raw_rec,
            "/p/IMG.JPG": jpeg_rec,
        }
        pair = FilePair(raw_path="/p/IMG.CR2", jpeg_path="/p/IMG.JPG", stem="IMG")

        mock_db = MagicMock()
        self.detector.link_pairs_in_db([pair], records, mock_db)

        assert raw_rec.pair_id == "jpeg-id-1"
        assert jpeg_rec.pair_id == "raw-id-1"
        assert mock_db.update_file_record.call_count == 2

    def test_skips_missing_records(self):
        """If a record is missing from the lookup dict, the pair is skipped."""
        raw_rec = _rec("/p/IMG.CR2", "/p", rec_id="raw-id-1")
        records = {"/p/IMG.CR2": raw_rec}  # JPEG not in dict

        pair = FilePair(raw_path="/p/IMG.CR2", jpeg_path="/p/IMG.JPG", stem="IMG")

        mock_db = MagicMock()
        self.detector.link_pairs_in_db([pair], records, mock_db)

        # Nothing should be updated
        assert raw_rec.pair_id is None
        mock_db.update_file_record.assert_not_called()

    def test_multiple_pairs_linked(self):
        """All pairs in the list get cross-linked."""
        raw1 = _rec("/p/A.CR2", "/p", rec_id="r1")
        jpg1 = _rec("/p/A.JPG", "/p", rec_id="j1")
        raw2 = _rec("/p/B.NEF", "/p", rec_id="r2")
        jpg2 = _rec("/p/B.jpg", "/p", rec_id="j2")

        records = {
            "/p/A.CR2": raw1,
            "/p/A.JPG": jpg1,
            "/p/B.NEF": raw2,
            "/p/B.jpg": jpg2,
        }
        pairs = [
            FilePair(raw_path="/p/A.CR2", jpeg_path="/p/A.JPG", stem="A"),
            FilePair(raw_path="/p/B.NEF", jpeg_path="/p/B.jpg", stem="B"),
        ]

        mock_db = MagicMock()
        self.detector.link_pairs_in_db(pairs, records, mock_db)

        assert raw1.pair_id == "j1"
        assert jpg1.pair_id == "r1"
        assert raw2.pair_id == "j2"
        assert jpg2.pair_id == "r2"
        assert mock_db.update_file_record.call_count == 4

    def test_empty_pairs_list(self):
        """No-op when called with an empty pairs list."""
        mock_db = MagicMock()
        self.detector.link_pairs_in_db([], {}, mock_db)
        mock_db.update_file_record.assert_not_called()


# ---------------------------------------------------------------------------
# FilePair dataclass
# ---------------------------------------------------------------------------

class TestFilePair:
    def test_fields(self):
        pair = FilePair(raw_path="/a.cr2", jpeg_path="/a.jpg", stem="a")
        assert pair.raw_path == "/a.cr2"
        assert pair.jpeg_path == "/a.jpg"
        assert pair.stem == "a"

    def test_equality(self):
        p1 = FilePair(raw_path="/a.cr2", jpeg_path="/a.jpg", stem="a")
        p2 = FilePair(raw_path="/a.cr2", jpeg_path="/a.jpg", stem="a")
        assert p1 == p2
