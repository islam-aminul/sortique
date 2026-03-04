"""Smart category suggestions for files stuck in Collection/ fallback."""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sortique.constants import DateSource, FileStatus, FileType
from sortique.data.file_system import FileSystemHelper

if TYPE_CHECKING:
    from sortique.data.database import Database
    from sortique.data.models import FileRecord
    from sortique.engine.categorizer import Categorizer
    from sortique.engine.metadata.date_parser import DateParser
    from sortique.engine.metadata.exif_extractor import ExifExtractor
    from sortique.engine.path_generator import PathGenerator


# ---------------------------------------------------------------------------
# Suggestion container
# ---------------------------------------------------------------------------

@dataclass
class ReviewSuggestion:
    """A smart category suggestion for a Collection/ file."""

    file_record: FileRecord
    suggested_category: str
    confidence: float  # 0.0 to 1.0
    reason: str  # human-readable explanation


# ---------------------------------------------------------------------------
# Social-media filename patterns (used as heuristic when no config handy)
# ---------------------------------------------------------------------------

_SOCIAL_PREFIXES = (
    "IMG-", "VID-",                # WhatsApp
    "FB_IMG_", "FB_VID_",          # Facebook
    "Screenshot_",                 # Android screenshots
    "signal-",                     # Signal
    "Telegram",                    # Telegram
)


# ---------------------------------------------------------------------------
# Reviewer
# ---------------------------------------------------------------------------

class CollectionReviewer:
    """Provides smart category suggestions for files stuck in Collection/."""

    def __init__(
        self,
        db: Database,
        categorizer: Categorizer,
        exif_extractor: ExifExtractor,
        date_parser: DateParser,
        path_generator: PathGenerator,
    ) -> None:
        self.db = db
        self.categorizer = categorizer
        self.exif_extractor = exif_extractor
        self.date_parser = date_parser
        self.path_generator = path_generator

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_review_items(self, session_id: str) -> list[ReviewSuggestion]:
        """Get all files in Collection/ category with smart suggestions.

        For each Collection file:

        1. Re-examine EXIF metadata (may have failed on first pass).
        2. Check dimensions for screenshot heuristic.
        3. Check filename for social-media patterns.
        4. Look at neighbouring files for context.
        5. Check whether filename contains a parseable date.
        6. Suggest best category with confidence and reason.

        If nothing better is found, suggest ``"Collection"`` with
        confidence 0.0.
        """
        records = self.db.get_file_records(session_id)
        collection_records = [
            r for r in records
            if r.category == "Collection"
            and r.status == FileStatus.COMPLETED
        ]

        if not collection_records:
            return []

        # Build per-directory context from *all* records.
        dir_categories = self._build_dir_context(records)

        suggestions: list[ReviewSuggestion] = []
        for rec in collection_records:
            suggestion = self._suggest(rec, dir_categories)
            suggestions.append(suggestion)

        return suggestions

    def reclassify(
        self,
        file_id: str,
        new_category: str,
        session_id: str,
    ) -> FileRecord:
        """Reclassify a single file: update category, regenerate
        destination path, and persist to the database.
        """
        records = self.db.get_file_records(session_id)
        rec = next((r for r in records if r.id == file_id), None)
        if rec is None:
            raise ValueError(f"FileRecord not found: {file_id}")

        rec.category = new_category

        # Regenerate destination path.
        stem = os.path.splitext(os.path.basename(rec.source_path))[0]
        ext = os.path.splitext(rec.source_path)[1]

        date_result = self.date_parser.extract_date(rec.source_path)
        exif = None
        if rec.file_type == FileType.IMAGE:
            exif = self.exif_extractor.extract(rec.source_path)

        rec.destination_path = self.path_generator.generate(
            category=new_category,
            original_filename=stem,
            original_ext=ext,
            date_result=date_result,
            exif=exif,
        )

        self.db.update_file_record(rec)
        return rec

    def reclassify_batch(
        self,
        reclassifications: list[tuple[str, str]],
        session_id: str,
    ) -> list[FileRecord]:
        """Batch reclassify: list of ``(file_id, new_category)`` tuples."""
        return [
            self.reclassify(file_id, new_cat, session_id)
            for file_id, new_cat in reclassifications
        ]

    # ------------------------------------------------------------------
    # Suggestion logic
    # ------------------------------------------------------------------

    def _suggest(
        self,
        rec: FileRecord,
        dir_categories: dict[str, Counter],
    ) -> ReviewSuggestion:
        """Generate the best suggestion for a single Collection file."""
        filename = os.path.basename(rec.source_path)

        # 1. Re-examine EXIF — maybe extraction failed on first pass.
        if rec.file_type == FileType.IMAGE:
            exif = self.exif_extractor.extract(rec.source_path)
            if exif.make is not None:
                return ReviewSuggestion(
                    file_record=rec,
                    suggested_category="Originals",
                    confidence=0.9,
                    reason=f"Camera metadata found: {exif.make}",
                )
            if exif.width and exif.height:
                if self.categorizer._matches_screenshot_resolution(
                    exif.width, exif.height,
                ):
                    return ReviewSuggestion(
                        file_record=rec,
                        suggested_category="Screenshots",
                        confidence=0.7,
                        reason="Dimensions match common screenshot resolution",
                    )

        # 2. Social-media filename patterns.
        for prefix in _SOCIAL_PREFIXES:
            if filename.startswith(prefix):
                return ReviewSuggestion(
                    file_record=rec,
                    suggested_category="Social Media",
                    confidence=0.7,
                    reason=f"Filename matches social media pattern: {prefix}*",
                )

        # 3. Filename contains a parseable date → suggest Export.
        date_result = self.date_parser.extract_date(rec.source_path)
        if date_result.date is not None and date_result.source == DateSource.PARSED:
            return ReviewSuggestion(
                file_record=rec,
                suggested_category="Export",
                confidence=date_result.confidence,
                reason="Date found in filename",
            )

        # 4. Neighbouring-file context — majority category from same dir.
        src_dir = os.path.normcase(os.path.normpath(rec.source_dir))
        counter = dir_categories.get(src_dir)
        if counter:
            # Exclude "Collection" itself from the vote.
            filtered = {k: v for k, v in counter.items() if k != "Collection"}
            if filtered:
                best_cat, best_count = max(filtered.items(), key=lambda x: x[1])
                total = sum(filtered.values())
                if best_count / total > 0.5:
                    return ReviewSuggestion(
                        file_record=rec,
                        suggested_category=best_cat,
                        confidence=0.4,
                        reason=f"Most files in same folder are categorised as {best_cat}",
                    )

        # 5. No suggestion — keep Collection.
        return ReviewSuggestion(
            file_record=rec,
            suggested_category="Collection",
            confidence=0.0,
            reason="No better category found",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_dir_context(
        records: list[FileRecord],
    ) -> dict[str, Counter]:
        """Build a per-directory category frequency counter."""
        dir_cats: dict[str, Counter] = {}
        for rec in records:
            if rec.status != FileStatus.COMPLETED or not rec.category:
                continue
            key = os.path.normcase(os.path.normpath(rec.source_dir))
            if key not in dir_cats:
                dir_cats[key] = Counter()
            dir_cats[key][rec.category] += 1
        return dir_cats
