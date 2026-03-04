"""Tests for CollectionReviewer and ReviewSuggestion."""

from __future__ import annotations

import os
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from sortique.constants import DateSource, ExifStatus, FileStatus, FileType
from sortique.data.models import FileRecord
from sortique.engine.metadata.date_parser import DateResult
from sortique.engine.metadata.exif_extractor import ExifResult
from sortique.service.collection_review import CollectionReviewer, ReviewSuggestion


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_record(
    source_path: str = "/src/photo.jpg",
    *,
    file_id: str | None = None,
    session_id: str = "sess-1",
    source_dir: str = "/src",
    file_type: FileType = FileType.IMAGE,
    category: str = "Collection",
    status: FileStatus = FileStatus.COMPLETED,
    destination_path: str | None = "/dst/Collection/Image/photo.jpg",
) -> FileRecord:
    rec = FileRecord(
        session_id=session_id,
        source_path=source_path,
        source_dir=source_dir,
        file_type=file_type,
        category=category,
        status=status,
        destination_path=destination_path,
    )
    if file_id is not None:
        rec.id = file_id
    return rec


def _build_reviewer(
    *,
    db_records: list[FileRecord] | None = None,
    exif_result: ExifResult | None = None,
    date_result: DateResult | None = None,
    generated_path: str = "/dst/NewCat/photo.jpg",
) -> CollectionReviewer:
    """Build a CollectionReviewer with mocked dependencies."""
    db = MagicMock()
    db.get_file_records.return_value = db_records or []

    categorizer = MagicMock()
    categorizer._matches_screenshot_resolution.return_value = False

    exif_extractor = MagicMock()
    exif_extractor.extract.return_value = exif_result or ExifResult()

    date_parser = MagicMock()
    date_parser.extract_date.return_value = date_result or DateResult()

    path_generator = MagicMock()
    path_generator.generate.return_value = generated_path

    return CollectionReviewer(
        db, categorizer, exif_extractor, date_parser, path_generator,
    )


# ---------------------------------------------------------------------------
# ReviewSuggestion dataclass
# ---------------------------------------------------------------------------

class TestReviewSuggestion:
    def test_fields(self):
        rec = _make_record()
        s = ReviewSuggestion(
            file_record=rec,
            suggested_category="Export",
            confidence=0.8,
            reason="Date found in filename",
        )
        assert s.file_record is rec
        assert s.suggested_category == "Export"
        assert s.confidence == 0.8
        assert s.reason == "Date found in filename"


# ---------------------------------------------------------------------------
# Suggestion: file with parseable date in filename
# ---------------------------------------------------------------------------

class TestSuggestionDateInFilename:
    def test_date_in_filename_suggests_export(self):
        """A Collection file whose filename contains a parseable date
        should get an Export suggestion."""
        rec = _make_record("/src/2024-06-15_holiday.jpg")

        date_result = DateResult(
            date=datetime(2024, 6, 15),
            source=DateSource.PARSED,
            confidence=0.8,
        )
        reviewer = _build_reviewer(
            db_records=[rec],
            date_result=date_result,
        )

        suggestions = reviewer.get_review_items("sess-1")

        assert len(suggestions) == 1
        s = suggestions[0]
        assert s.suggested_category == "Export"
        assert s.confidence == 0.8
        assert "Date found in filename" in s.reason

    def test_no_date_no_metadata_suggests_collection(self):
        """When nothing better is found, suggestion stays Collection."""
        rec = _make_record("/src/random_file.jpg")

        reviewer = _build_reviewer(db_records=[rec])

        suggestions = reviewer.get_review_items("sess-1")

        assert len(suggestions) == 1
        s = suggestions[0]
        assert s.suggested_category == "Collection"
        assert s.confidence == 0.0
        assert "No better category" in s.reason

    def test_social_media_filename(self):
        """Filenames with social-media prefixes get Social Media suggestion."""
        rec = _make_record("/src/IMG-20240615-WA0001.jpg")

        reviewer = _build_reviewer(db_records=[rec])

        suggestions = reviewer.get_review_items("sess-1")

        assert len(suggestions) == 1
        assert suggestions[0].suggested_category == "Social Media"
        assert suggestions[0].confidence == 0.7

    def test_camera_metadata_suggests_originals(self):
        """Re-examined EXIF with camera make → Originals suggestion."""
        rec = _make_record("/src/photo.jpg")

        exif = ExifResult(status=ExifStatus.OK, make="Canon", model="EOS R5")
        reviewer = _build_reviewer(db_records=[rec], exif_result=exif)

        suggestions = reviewer.get_review_items("sess-1")

        assert len(suggestions) == 1
        assert suggestions[0].suggested_category == "Originals"
        assert suggestions[0].confidence == 0.9
        assert "Canon" in suggestions[0].reason

    def test_screenshot_dimensions_suggest_screenshots(self):
        """Matching screenshot resolution → Screenshots suggestion."""
        rec = _make_record("/src/screen.png")

        exif = ExifResult(status=ExifStatus.NONE, width=1920, height=1080)
        reviewer = _build_reviewer(db_records=[rec], exif_result=exif)
        reviewer.categorizer._matches_screenshot_resolution.return_value = True

        suggestions = reviewer.get_review_items("sess-1")

        assert len(suggestions) == 1
        assert suggestions[0].suggested_category == "Screenshots"
        assert suggestions[0].confidence == 0.7

    def test_neighbouring_context_suggestion(self):
        """When most files in the same dir are Originals, suggest Originals."""
        collection_rec = _make_record(
            "/src/unknown.jpg",
            source_dir="/src",
        )
        # Neighbouring files already categorised.
        orig_a = _make_record(
            "/src/a.jpg",
            source_dir="/src",
            category="Originals",
        )
        orig_b = _make_record(
            "/src/b.jpg",
            source_dir="/src",
            category="Originals",
        )

        all_records = [collection_rec, orig_a, orig_b]
        reviewer = _build_reviewer(db_records=all_records)

        suggestions = reviewer.get_review_items("sess-1")

        assert len(suggestions) == 1
        assert suggestions[0].suggested_category == "Originals"
        assert suggestions[0].confidence == 0.4
        assert "same folder" in suggestions[0].reason

    def test_skips_non_collection_records(self):
        """Only Collection files get reviewed."""
        rec_originals = _make_record(
            "/src/a.jpg", category="Originals",
        )
        rec_collection = _make_record(
            "/src/b.jpg", category="Collection",
        )

        reviewer = _build_reviewer(db_records=[rec_originals, rec_collection])

        suggestions = reviewer.get_review_items("sess-1")

        assert len(suggestions) == 1
        assert suggestions[0].file_record is rec_collection

    def test_skips_non_completed_records(self):
        """Only COMPLETED Collection files get reviewed."""
        rec = _make_record(
            "/src/a.jpg",
            category="Collection",
            status=FileStatus.SKIPPED,
        )

        reviewer = _build_reviewer(db_records=[rec])

        suggestions = reviewer.get_review_items("sess-1")

        assert len(suggestions) == 0

    def test_empty_session(self):
        reviewer = _build_reviewer(db_records=[])

        suggestions = reviewer.get_review_items("sess-1")

        assert suggestions == []


# ---------------------------------------------------------------------------
# Reclassify updates category and path
# ---------------------------------------------------------------------------

class TestReclassify:
    def test_updates_category_and_path(self):
        rec = _make_record("/src/photo.jpg", file_id="file-1")
        reviewer = _build_reviewer(
            db_records=[rec],
            generated_path="/dst/Export/2024/photo.jpg",
        )

        result = reviewer.reclassify("file-1", "Export", "sess-1")

        assert result.category == "Export"
        assert result.destination_path == "/dst/Export/2024/photo.jpg"
        reviewer.db.update_file_record.assert_called_once_with(result)

    def test_reclassify_calls_path_generator(self):
        rec = _make_record("/src/photo.jpg", file_id="file-1")
        date_result = DateResult(
            date=datetime(2024, 6, 15),
            source=DateSource.PARSED,
            confidence=0.8,
        )
        reviewer = _build_reviewer(
            db_records=[rec],
            date_result=date_result,
            generated_path="/dst/Screenshots/photo.jpg",
        )

        reviewer.reclassify("file-1", "Screenshots", "sess-1")

        reviewer.path_generator.generate.assert_called_once()
        call_kwargs = reviewer.path_generator.generate.call_args
        assert call_kwargs[1]["category"] == "Screenshots" or call_kwargs[0][0] == "Screenshots"

    def test_reclassify_not_found_raises(self):
        reviewer = _build_reviewer(db_records=[])

        with pytest.raises(ValueError, match="FileRecord not found"):
            reviewer.reclassify("nonexistent", "Export", "sess-1")

    def test_reclassify_persists_to_db(self):
        rec = _make_record("/src/photo.jpg", file_id="file-1")
        reviewer = _build_reviewer(db_records=[rec])

        reviewer.reclassify("file-1", "Originals", "sess-1")

        reviewer.db.update_file_record.assert_called_once()
        saved = reviewer.db.update_file_record.call_args[0][0]
        assert saved.category == "Originals"


# ---------------------------------------------------------------------------
# Batch reclassify
# ---------------------------------------------------------------------------

class TestBatchReclassify:
    def test_batch_reclassifies_multiple(self):
        rec_a = _make_record("/src/a.jpg", file_id="file-a")
        rec_b = _make_record("/src/b.jpg", file_id="file-b")
        rec_c = _make_record("/src/c.jpg", file_id="file-c")

        reviewer = _build_reviewer(
            db_records=[rec_a, rec_b, rec_c],
            generated_path="/dst/new/file.jpg",
        )

        results = reviewer.reclassify_batch(
            [("file-a", "Export"), ("file-b", "Screenshots"), ("file-c", "Originals")],
            "sess-1",
        )

        assert len(results) == 3
        assert results[0].category == "Export"
        assert results[1].category == "Screenshots"
        assert results[2].category == "Originals"
        assert reviewer.db.update_file_record.call_count == 3

    def test_batch_empty_list(self):
        reviewer = _build_reviewer()

        results = reviewer.reclassify_batch([], "sess-1")

        assert results == []

    def test_batch_single_item(self):
        rec = _make_record("/src/a.jpg", file_id="file-a")
        reviewer = _build_reviewer(
            db_records=[rec],
            generated_path="/dst/Export/a.jpg",
        )

        results = reviewer.reclassify_batch(
            [("file-a", "Export")],
            "sess-1",
        )

        assert len(results) == 1
        assert results[0].category == "Export"
