"""RAW+JPEG pair detection for cameras that produce both simultaneously."""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sortique.data.database import Database
    from sortique.data.models import FileRecord


# ---------------------------------------------------------------------------
# Extension sets
# ---------------------------------------------------------------------------

RAW_EXTENSIONS: frozenset[str] = frozenset({
    ".cr2", ".cr3", ".nef", ".nrw", ".arw", ".srf", ".sr2",
    ".dng", ".orf", ".erf", ".raf", ".rw2", ".rwl", ".pef",
    ".ptx", ".srw", ".x3f", ".3fr", ".mef", ".mos", ".mrw",
    ".kdc", ".dcr", ".iiq", ".gpr", ".raw",
})

JPEG_EXTENSIONS: frozenset[str] = frozenset({".jpg", ".jpeg"})


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class FilePair:
    """A RAW+JPEG file pair sharing the same filename stem."""

    raw_path: str
    jpeg_path: str
    stem: str  # shared filename stem (without extension)


# ---------------------------------------------------------------------------
# PairDetector
# ---------------------------------------------------------------------------

class PairDetector:
    """Detects RAW+JPEG file pairs shot by cameras that produce both simultaneously."""

    def detect_pairs(self, file_records: list[FileRecord]) -> list[FilePair]:
        """Find RAW+JPEG pairs among *file_records*.

        Algorithm:

        1. Group files by ``(source_dir, filename_stem)`` — both
           lower-cased for case-insensitive matching.
        2. For each group, check if there is **exactly one** RAW-extension
           file and **exactly one** JPEG-extension file.
        3. If so, create a :class:`FilePair`.

        Pairs **must** reside in the same source directory.
        """
        # bucket key: (normalised source_dir, lower-cased stem)
        buckets: dict[
            tuple[str, str],
            dict[str, list[FileRecord]],
        ] = defaultdict(lambda: {"raw": [], "jpeg": []})

        for rec in file_records:
            ext = os.path.splitext(rec.source_path)[1].lower()
            stem = os.path.splitext(os.path.basename(rec.source_path))[0].lower()
            src_dir = os.path.normcase(os.path.normpath(rec.source_dir))

            key = (src_dir, stem)

            if ext in RAW_EXTENSIONS:
                buckets[key]["raw"].append(rec)
            elif ext in JPEG_EXTENSIONS:
                buckets[key]["jpeg"].append(rec)

        pairs: list[FilePair] = []
        for (_, stem), group in buckets.items():
            if len(group["raw"]) == 1 and len(group["jpeg"]) == 1:
                raw_rec = group["raw"][0]
                jpeg_rec = group["jpeg"][0]
                # Use the original-case stem from the RAW file.
                original_stem = os.path.splitext(
                    os.path.basename(raw_rec.source_path),
                )[0]
                pairs.append(FilePair(
                    raw_path=raw_rec.source_path,
                    jpeg_path=jpeg_rec.source_path,
                    stem=original_stem,
                ))

        # Deterministic ordering.
        pairs.sort(key=lambda p: p.raw_path)
        return pairs

    def link_pairs_in_db(
        self,
        pairs: list[FilePair],
        records: dict[str, FileRecord],
        db: Database,
    ) -> None:
        """Update ``FileRecord.pair_id`` to cross-link paired files.

        *records* is a ``{source_path: FileRecord}`` lookup.

        For every pair the RAW record's ``pair_id`` is set to the JPEG
        record's ``id``, and vice-versa.  Both records are persisted via
        ``db.update_file_record``.
        """
        for pair in pairs:
            raw_rec = records.get(pair.raw_path)
            jpeg_rec = records.get(pair.jpeg_path)

            if raw_rec is None or jpeg_rec is None:
                continue

            raw_rec.pair_id = jpeg_rec.id
            jpeg_rec.pair_id = raw_rec.id

            db.update_file_record(raw_rec)
            db.update_file_record(jpeg_rec)
