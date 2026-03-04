"""Tests for sortique.engine.path_generator."""

from __future__ import annotations

import os
from datetime import datetime

import pytest

from sortique.constants import DateSource, MAX_CONFLICT_ATTEMPTS
from sortique.data.config_manager import ConfigManager
from sortique.engine.metadata.date_parser import DateResult
from sortique.engine.metadata.exif_extractor import ExifResult
from sortique.engine.path_generator import PathGenerator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dr(dt: datetime | None = None, **kw) -> DateResult:
    """Shorthand DateResult builder."""
    return DateResult(date=dt, source=kw.get("source", DateSource.METADATA))


def _exif(**kw) -> ExifResult:
    """Shorthand ExifResult builder."""
    return ExifResult(**kw)


J = os.path.join  # alias for readability in assertions


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def config(tmp_path):
    return ConfigManager(config_dir=str(tmp_path / "cfg"))


@pytest.fixture()
def dest(tmp_path):
    """A concrete destination directory on disk."""
    d = tmp_path / "Sorted"
    d.mkdir()
    return str(d)


@pytest.fixture()
def gen(config, dest):
    return PathGenerator(config, dest)


# ===========================================================================
# 1.  generate_filename
# ===========================================================================

class TestGenerateFilename:
    """Adaptive filename template."""

    def test_full_metadata(self, gen):
        """Date + camera + original name."""
        dr = _dr(datetime(2024, 3, 15, 14, 30, 0))
        exif = _exif(make="Canon", model="EOS R5")
        name = gen.generate_filename("IMG_001", ".jpg", dr, exif)
        assert name == "2024-03-15 14-30-00 -- Canon - EOS R5 -- IMG_001.jpg"

    def test_redundant_make_in_model(self, gen):
        """Model starts with make → make prefix elided."""
        dr = _dr(datetime(2024, 1, 1, 12, 0, 0))
        exif = _exif(make="Apple", model="Apple iPhone 15 Pro")
        name = gen.generate_filename("IMG_001", ".jpg", dr, exif)
        assert name == "2024-01-01 12-00-00 -- Apple iPhone 15 Pro -- IMG_001.jpg"

    def test_no_camera(self, gen):
        """Date present but no camera make/model."""
        dr = _dr(datetime(2024, 6, 1, 9, 0, 0))
        name = gen.generate_filename("photo", ".png", dr, None)
        assert name == "2024-06-01 09-00-00 -- photo.png"

    def test_no_date(self, gen):
        """No date at all → just original name."""
        name = gen.generate_filename("DSC_0042", ".jpg", None, None)
        assert name == "DSC_0042.jpg"

    def test_no_date_with_camera(self, gen):
        """Camera but no date — make/model still appears."""
        exif = _exif(make="Nikon", model="D850")
        name = gen.generate_filename("DSC_0042", ".jpg", None, exif)
        assert name == "Nikon - D850 -- DSC_0042.jpg"

    def test_burst_naming(self, gen):
        """Burst appends -NNN after the time component."""
        dr = _dr(datetime(2024, 3, 15, 14, 30, 0))
        exif = _exif(make="Samsung", model="SM-G998B")
        name = gen.generate_filename(
            "IMG_001", ".jpg", dr, exif, is_burst=True, burst_index=5,
        )
        assert name == "2024-03-15 14-30-00-005 -- Samsung - SM-G998B -- IMG_001.jpg"

    def test_burst_index_zero(self, gen):
        dr = _dr(datetime(2024, 1, 1, 0, 0, 0))
        name = gen.generate_filename(
            "burst", ".jpg", dr, None, is_burst=True, burst_index=0,
        )
        assert name == "2024-01-01 00-00-00-000 -- burst.jpg"

    def test_ext_without_leading_dot(self, gen):
        """Extension passed as 'jpg' (no dot) → dot auto-prepended."""
        name = gen.generate_filename("file", "jpg", None, None)
        assert name == "file.jpg"

    def test_empty_ext(self, gen):
        name = gen.generate_filename("README", "", None, None)
        assert name == "README"

    def test_sanitization_strips_illegal_chars(self, gen):
        """Illegal characters in the original name are replaced."""
        name = gen.generate_filename('photo: "best"', ".jpg", None, None)
        # On Windows colons and quotes are replaced with underscores.
        assert ":" not in name
        assert '"' not in name
        assert name.endswith(".jpg")

    def test_date_result_with_none_date(self, gen):
        """DateResult object exists but .date is None."""
        dr = DateResult()  # date=None
        name = gen.generate_filename("img", ".jpg", dr, None)
        assert name == "img.jpg"


# ===========================================================================
# 2.  _format_make_model
# ===========================================================================

class TestFormatMakeModel:

    def test_both_present(self, gen):
        assert gen._format_make_model("Nikon", "D850") == "Nikon - D850"

    def test_redundant_prefix(self, gen):
        assert gen._format_make_model("Canon", "Canon EOS R5") == "Canon EOS R5"

    def test_case_insensitive_redundancy(self, gen):
        assert gen._format_make_model("SONY", "Sony ILCE-7M4") == "Sony ILCE-7M4"

    def test_make_only(self, gen):
        assert gen._format_make_model("Apple", None) == "Apple"

    def test_model_only(self, gen):
        assert gen._format_make_model(None, "iPhone 15 Pro") == "iPhone 15 Pro"

    def test_both_none(self, gen):
        assert gen._format_make_model(None, None) is None

    def test_whitespace_stripped(self, gen):
        assert gen._format_make_model("  DJI  ", "  Mavic 3  ") == "DJI - Mavic 3"

    def test_sanitized_for_filesystem(self, gen):
        result = gen._format_make_model("Make:X", 'Model?"Y')
        assert ":" not in result
        assert "?" not in result
        assert '"' not in result


# ===========================================================================
# 3.  _build_category_path
# ===========================================================================

class TestBuildCategoryPath:
    """Every documented category → folder mapping."""

    def test_originals_full(self, gen):
        p = gen._build_category_path("Originals", 2024, "Canon", "EOS R5")
        assert p == J("Originals", "Canon - EOS R5", "2024")

    def test_originals_redundant_make(self, gen):
        p = gen._build_category_path("Originals", 2024, "Apple", "Apple iPhone 15")
        assert p == J("Originals", "Apple iPhone 15", "2024")

    def test_originals_no_make_model(self, gen):
        p = gen._build_category_path("Originals", 2024, None, None)
        assert p == J("Originals", "2024")

    def test_originals_no_year(self, gen):
        p = gen._build_category_path("Originals", None, "Sony", "A7R IV")
        assert p == J("Originals", "Sony - A7R IV")

    def test_originals_no_metadata(self, gen):
        p = gen._build_category_path("Originals", None, None, None)
        assert p == "Originals"

    def test_raw_full(self, gen):
        p = gen._build_category_path("RAW", 2023, "Nikon", "Z8")
        assert p == J("RAW", "Nikon - Z8", "2023")

    def test_raw_no_year(self, gen):
        p = gen._build_category_path("RAW", None, "Canon", "EOS R5")
        assert p == J("RAW", "Canon - EOS R5")

    def test_edited_with_year(self, gen):
        assert gen._build_category_path("Edited", 2024, None, None) == J("Edited", "2024")

    def test_edited_no_year(self, gen):
        assert gen._build_category_path("Edited", None, None, None) == "Edited"

    def test_screenshots(self, gen):
        assert gen._build_category_path("Screenshots", 2024, None, None) == "Screenshots"

    def test_social_media(self, gen):
        assert gen._build_category_path("Social Media", None, None, None) == "Social Media"

    def test_hidden(self, gen):
        assert gen._build_category_path("Hidden", None, None, None) == "Hidden"

    def test_export_with_year(self, gen):
        assert gen._build_category_path("Export", 2024, None, None) == J("Export", "2024")

    def test_export_no_year(self, gen):
        assert gen._build_category_path("Export", None, None, None) == "Export"

    def test_motion_photos_with_year(self, gen):
        p = gen._build_category_path("Motion Photos", 2024, None, None)
        assert p == J("Motion Photos", "2024")

    def test_movies(self, gen):
        assert gen._build_category_path("Movies", None, None, None) == "Movies"

    def test_originals_unknown_with_year(self, gen):
        p = gen._build_category_path("Originals/Unknown", 2024, None, None)
        assert p == J("Originals", "Unknown", "2024")

    def test_originals_unknown_no_year(self, gen):
        p = gen._build_category_path("Originals/Unknown", None, None, None)
        assert p == J("Originals", "Unknown")

    def test_voice_notes_with_year(self, gen):
        p = gen._build_category_path("Voice Notes", 2024, None, None)
        assert p == J("Voice Notes", "2024")

    def test_whatsapp_with_year(self, gen):
        p = gen._build_category_path("WhatsApp", 2024, None, None)
        assert p == J("WhatsApp", "2024")

    def test_songs(self, gen):
        assert gen._build_category_path("Songs", None, None, None) == "Songs"

    def test_documents_pdf(self, gen):
        p = gen._build_category_path("Documents/PDF", None, None, None)
        assert p == J("Documents", "PDF")

    def test_documents_code(self, gen):
        p = gen._build_category_path("Documents/Code", None, None, None)
        assert p == J("Documents", "Code")

    def test_collection_image(self, gen):
        """Pre-expanded Collection path passed from generate()."""
        p = gen._build_category_path("Collection/Image", None, None, None)
        assert p == J("Collection", "Image")


# ===========================================================================
# 4.  generate  (full path integration)
# ===========================================================================

class TestGenerate:
    """End-to-end path generation."""

    def test_full_path_originals(self, gen, dest):
        dr = _dr(datetime(2024, 3, 15, 14, 30, 0))
        exif = _exif(make="Canon", model="EOS R5")
        path = gen.generate("Originals", "IMG_001", ".jpg", dr, exif)
        expected = J(
            dest, "Originals", "Canon - EOS R5", "2024",
            "2024-03-15 14-30-00 -- Canon - EOS R5 -- IMG_001.jpg",
        )
        assert path == expected

    def test_path_no_camera(self, gen, dest):
        dr = _dr(datetime(2024, 6, 1, 9, 0, 0))
        path = gen.generate("Edited", "photo", ".png", dr, None)
        expected = J(
            dest, "Edited", "2024",
            "2024-06-01 09-00-00 -- photo.png",
        )
        assert path == expected

    def test_path_no_date(self, gen, dest):
        path = gen.generate("Screenshots", "capture", ".png", None, None)
        expected = J(dest, "Screenshots", "capture.png")
        assert path == expected

    def test_path_no_metadata(self, gen, dest):
        path = gen.generate("Collection", "mystery", ".dat", None, None)
        expected = J(dest, "Collection", "Unknown", "mystery.dat")
        assert path == expected

    def test_collection_jpg(self, gen, dest):
        path = gen.generate("Collection", "photo", ".jpg", None, None)
        expected = J(dest, "Collection", "Image", "photo.jpg")
        assert path == expected

    def test_collection_mp4(self, gen, dest):
        path = gen.generate("Collection", "clip", ".mp4", None, None)
        expected = J(dest, "Collection", "Video", "clip.mp4")
        assert path == expected

    def test_collection_mp3(self, gen, dest):
        path = gen.generate("Collection", "song", ".mp3", None, None)
        expected = J(dest, "Collection", "Audio", "song.mp3")
        assert path == expected

    def test_export_with_date(self, gen, dest):
        dr = _dr(datetime(2024, 3, 15, 14, 30, 0))
        exif = _exif(make="Canon", model="EOS R5")
        path = gen.generate(
            "Originals", "IMG_001", ".jpg", dr, exif, is_export=True,
        )
        expected = J(
            dest, "Exports", "2024",
            "2024-03-15 14-30-00 -- Canon - EOS R5 -- IMG_001.jpg",
        )
        assert path == expected

    def test_export_no_date(self, gen, dest):
        path = gen.generate("Originals", "IMG_001", ".jpg", None, None, is_export=True)
        expected = J(dest, "Exports", "IMG_001.jpg")
        assert path == expected

    def test_burst_in_full_path(self, gen, dest):
        dr = _dr(datetime(2024, 3, 15, 14, 30, 0))
        exif = _exif(make="Samsung", model="SM-G998B")
        path = gen.generate(
            "Originals", "IMG_001", ".jpg", dr, exif,
            is_burst=True, burst_index=3,
        )
        expected = J(
            dest, "Originals", "Samsung - SM-G998B", "2024",
            "2024-03-15 14-30-00-003 -- Samsung - SM-G998B -- IMG_001.jpg",
        )
        assert path == expected

    def test_documents_pdf(self, gen, dest):
        path = gen.generate("Documents/PDF", "report", ".pdf", None, None)
        expected = J(dest, "Documents", "PDF", "report.pdf")
        assert path == expected

    def test_raw_with_full_metadata(self, gen, dest):
        dr = _dr(datetime(2023, 12, 25, 8, 0, 0))
        exif = _exif(make="Nikon", model="Z8")
        path = gen.generate("RAW", "DSC_001", ".nef", dr, exif)
        expected = J(
            dest, "RAW", "Nikon - Z8", "2023",
            "2023-12-25 08-00-00 -- Nikon - Z8 -- DSC_001.nef",
        )
        assert path == expected

    def test_originals_unknown_with_year(self, gen, dest):
        dr = _dr(datetime(2024, 7, 4, 0, 0, 0))
        path = gen.generate("Originals/Unknown", "clip", ".mp4", dr, None)
        expected = J(
            dest, "Originals", "Unknown", "2024",
            "2024-07-04 00-00-00 -- clip.mp4",
        )
        assert path == expected

    def test_social_media(self, gen, dest):
        path = gen.generate("Social Media", "IMG-WA001", ".jpg", None, None)
        expected = J(dest, "Social Media", "IMG-WA001.jpg")
        assert path == expected

    def test_voice_notes(self, gen, dest):
        dr = _dr(datetime(2024, 2, 14, 18, 30, 0))
        path = gen.generate("Voice Notes", "Recording_001", ".m4a", dr, None)
        expected = J(
            dest, "Voice Notes", "2024",
            "2024-02-14 18-30-00 -- Recording_001.m4a",
        )
        assert path == expected

    def test_songs(self, gen, dest):
        path = gen.generate("Songs", "track01", ".mp3", None, None)
        expected = J(dest, "Songs", "track01.mp3")
        assert path == expected


# ===========================================================================
# 5.  resolve_conflict
# ===========================================================================

class TestResolveConflict:
    """Conflict resolution by numeric suffix."""

    def test_no_conflict(self, gen, dest):
        target = J(dest, "photo.jpg")
        assert gen.resolve_conflict(target) == target

    def test_first_conflict(self, gen, dest):
        target = J(dest, "photo.jpg")
        with open(target, "w") as f:
            f.write("x")
        resolved = gen.resolve_conflict(target)
        assert resolved == J(dest, "photo_1.jpg")

    def test_multiple_conflicts(self, gen, dest):
        base = J(dest, "photo.jpg")
        # Create original + _1 + _2
        for suffix in ("", "_1", "_2"):
            stem, ext = os.path.splitext(base)
            path = f"{stem}{suffix}{ext}" if suffix else base
            with open(path, "w") as f:
                f.write("x")
        resolved = gen.resolve_conflict(base)
        assert resolved == J(dest, "photo_3.jpg")

    def test_preserves_extension(self, gen, dest):
        target = J(dest, "clip.mp4")
        with open(target, "w") as f:
            f.write("x")
        resolved = gen.resolve_conflict(target)
        assert resolved.endswith("_1.mp4")

    def test_no_extension(self, gen, dest):
        target = J(dest, "README")
        with open(target, "w") as f:
            f.write("x")
        resolved = gen.resolve_conflict(target)
        assert resolved == J(dest, "README_1")

    def test_exhaustion_raises(self, gen, dest, monkeypatch):
        """All attempts exhausted → FileExistsError."""
        monkeypatch.setattr(
            "sortique.engine.path_generator.MAX_CONFLICT_ATTEMPTS", 3,
        )
        target = J(dest, "photo.jpg")
        for suffix in ("", "_1", "_2", "_3"):
            stem, ext = os.path.splitext(target)
            path = f"{stem}{suffix}{ext}" if suffix else target
            with open(path, "w") as f:
                f.write("x")
        with pytest.raises(FileExistsError):
            gen.resolve_conflict(target)


# ===========================================================================
# 6.  Filename sanitisation integration
# ===========================================================================

class TestSanitisationIntegration:
    """Verify that sanitize_filename is applied end-to-end."""

    def test_colons_in_make(self, gen, dest):
        dr = _dr(datetime(2024, 1, 1, 0, 0, 0))
        exif = _exif(make="Make:X", model="Model")
        path = gen.generate("Originals", "img", ".jpg", dr, exif)
        filename = os.path.basename(path)
        assert ":" not in filename

    def test_illegal_chars_in_original_name(self, gen, dest):
        path = gen.generate("Screenshots", 'my<file>', ".png", None, None)
        filename = os.path.basename(path)
        assert "<" not in filename
        assert ">" not in filename

    def test_make_model_folder_sanitised(self, gen, dest):
        dr = _dr(datetime(2024, 1, 1, 0, 0, 0))
        exif = _exif(make='Bad/"Make"', model="OK")
        path = gen.generate("Originals", "img", ".jpg", dr, exif)
        # The folder component should not contain quotes.
        folder = os.path.dirname(path)
        assert '"' not in folder


# ===========================================================================
# 7.  Edge cases
# ===========================================================================

class TestEdgeCases:

    def test_ext_normalised_with_dot(self, gen, dest):
        path = gen.generate("Screenshots", "img", "png", None, None)
        assert path.endswith(".png")

    def test_empty_ext(self, gen, dest):
        path = gen.generate("Collection", "file", "", None, None)
        assert os.path.basename(path) == "file"

    def test_year_omitted_when_no_date(self, gen, dest):
        exif = _exif(make="Canon", model="EOS R5")
        path = gen.generate("Originals", "img", ".jpg", None, exif)
        # The "2024" year folder must NOT appear.
        parts = path.split(os.sep)
        # After dest: "Originals" / "Canon - EOS R5" / filename
        assert "2024" not in parts

    def test_make_model_omitted_when_missing(self, gen, dest):
        dr = _dr(datetime(2024, 1, 1, 0, 0, 0))
        path = gen.generate("Originals", "img", ".jpg", dr, None)
        parts = path.split(os.sep)
        # Directly Originals / 2024 / filename — no make-model folder.
        orig_idx = parts.index("Originals")
        assert parts[orig_idx + 1] == "2024"

    def test_movies_no_year_subfolder(self, gen, dest):
        """Movies is a static category — no year sub-folder."""
        dr = _dr(datetime(2024, 1, 1, 0, 0, 0))
        path = gen.generate("Movies", "movie", ".mp4", dr, None)
        dirname = os.path.dirname(path)
        assert dirname == J(dest, "Movies")
