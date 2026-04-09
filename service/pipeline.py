"""Sortique 13-stage file processing pipeline."""

from __future__ import annotations

import fnmatch
import logging
import os
import threading
from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING

from sortique.constants import FileStatus, FileType
from sortique.data.database import Database
from sortique.data.file_system import FileSystemHelper
from sortique.data.hash_manifest import HashManifest
from sortique.data.models import FileRecord

if TYPE_CHECKING:
    from sortique.engine.categorizer import Categorizer
    from sortique.engine.dedup import DedupEngine
    from sortique.engine.detector import ContentDetector
    from sortique.engine.hasher import FileHasher
    from sortique.engine.metadata.audio_metadata import AudioMetadataExtractor
    from sortique.engine.metadata.musicbrainz_client import MusicBrainzClient
    from sortique.engine.metadata.date_parser import DateParser
    from sortique.engine.metadata.exif_extractor import ExifExtractor
    from sortique.engine.metadata.video_metadata import VideoMetadataExtractor
    from sortique.engine.path_generator import PathGenerator
    from sortique.engine.processors.audio_processor import AudioProcessor
    from sortique.engine.processors.document_processor import DocumentProcessor
    from sortique.engine.processors.image_processor import ImageProcessor
    from sortique.engine.processors.video_processor import VideoProcessor

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pipeline stage enum
# ---------------------------------------------------------------------------

class PipelineStage(IntEnum):
    """Sequential processing stages (1-based for DB storage)."""

    INIT = 1
    CHECK_PROCESSED = 2
    PATTERN_SKIP = 3
    DETECT = 4
    HASH = 5
    DEDUP = 6
    METADATA = 7
    DATE_RESOLVE = 8
    BUILD_PATH = 9
    COPY = 10
    PAIR = 11
    CLEANUP = 12
    VERIFY = 13


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class PipelineResult:
    """Outcome of running a single file through the pipeline."""

    file_id: str = ""
    final_status: FileStatus = FileStatus.PENDING
    skip_reason: str | None = None
    error_message: str | None = None
    stages_completed: int = 0


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class Pipeline:
    """13-stage file processing pipeline with per-file error isolation,
    resume support, and an optional dry-run mode.
    """

    STAGES: list[tuple[PipelineStage, str]] = [
        (PipelineStage.INIT, "_stage_init"),
        (PipelineStage.CHECK_PROCESSED, "_stage_check_processed"),
        (PipelineStage.PATTERN_SKIP, "_stage_pattern_skip"),
        (PipelineStage.DETECT, "_stage_detect"),
        (PipelineStage.HASH, "_stage_hash"),
        (PipelineStage.DEDUP, "_stage_dedup"),
        (PipelineStage.METADATA, "_stage_metadata"),
        (PipelineStage.DATE_RESOLVE, "_stage_date_resolve"),
        (PipelineStage.BUILD_PATH, "_stage_build_path"),
        (PipelineStage.COPY, "_stage_copy"),
        (PipelineStage.PAIR, "_stage_pair"),
        (PipelineStage.CLEANUP, "_stage_cleanup"),
        (PipelineStage.VERIFY, "_stage_verify"),
    ]

    def __init__(
        self,
        db: Database,
        session_id: str,
        *,
        detector: ContentDetector,
        hasher: FileHasher,
        dedup: DedupEngine,
        categorizer: Categorizer,
        path_generator: PathGenerator,
        exif_extractor: ExifExtractor,
        date_parser: DateParser,
        video_extractor: VideoMetadataExtractor,
        audio_extractor: AudioMetadataExtractor,
        musicbrainz_client: MusicBrainzClient | None = None,
        image_processor: ImageProcessor,
        video_processor: VideoProcessor,
        audio_processor: AudioProcessor,
        document_processor: DocumentProcessor,
        dry_run: bool = False,
        skip_filename_patterns: list[str] = (),
    ) -> None:
        self._db = db
        self._session_id = session_id
        self._dry_run = dry_run

        # Engine dependencies
        self._detector = detector
        self._hasher = hasher
        self._dedup = dedup
        self._categorizer = categorizer
        self._path_gen = path_generator
        self._exif_extractor = exif_extractor
        self._date_parser = date_parser
        self._video_extractor = video_extractor
        self._audio_extractor = audio_extractor
        self._musicbrainz_client = musicbrainz_client
        self._image_processor = image_processor
        self._video_processor = video_processor
        self._audio_processor = audio_processor
        self._document_processor = document_processor

        # Filename-based skip patterns (fnmatch globs).
        self._skip_filename_patterns: list[str] = list(skip_filename_patterns)

        # Load portable hash manifest for cross-machine dedup.
        manifest = HashManifest(self._path_gen.destination_root)
        self._dedup.load_manifest(manifest)

        # Per-file transient state — stored in thread-local storage so that
        # concurrent workers each get their own isolated copy.
        self._local = threading.local()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_file(self, record: FileRecord) -> PipelineResult:
        """Run *record* through all applicable pipeline stages.

        Stages before ``record.pipeline_stage`` are skipped (resume).
        On error the exception is caught, recorded, and the result returned
        immediately so that other files in a batch are unaffected.
        """
        result = PipelineResult(file_id=record.id)
        start_stage = record.pipeline_stage

        # Reset per-file transient state (thread-local).
        self._local.exif_result = None
        self._local.date_result = None
        self._local.video_meta = None
        self._local.audio_meta = None

        for stage_num, method_name in self.STAGES:
            # Resume support: skip already-completed stages.
            if stage_num < start_stage:
                result.stages_completed = stage_num
                continue

            # Mark the file as processing at this stage.
            record.pipeline_stage = stage_num
            record.status = FileStatus.PROCESSING
            if not self._dry_run:
                self._db.update_file_stage(
                    record.id, stage_num, FileStatus.PROCESSING,
                )

            # Execute the stage with error isolation.
            try:
                stage_fn = getattr(self, method_name)
                ok, reason = stage_fn(record)
            except Exception as exc:
                record.status = FileStatus.ERROR
                record.error_message = str(exc)
                result.final_status = FileStatus.ERROR
                result.error_message = str(exc)
                result.stages_completed = stage_num - 1
                if not self._dry_run:
                    self._db.update_file_record(record)
                return result

            if not ok:
                # Stage signalled a skip.
                record.status = FileStatus.SKIPPED
                record.skip_reason = reason
                result.final_status = FileStatus.SKIPPED
                result.skip_reason = reason
                result.stages_completed = stage_num
                if not self._dry_run:
                    self._db.update_file_record(record)
                return result

            result.stages_completed = stage_num

        # Every stage passed — mark completed.
        record.status = FileStatus.COMPLETED
        result.final_status = FileStatus.COMPLETED
        if not self._dry_run:
            self._db.update_file_record(record)
        return result

    def process_batch(
        self, records: list[FileRecord],
    ) -> list[PipelineResult]:
        """Process a list of file records sequentially.

        Each file is isolated: an error or skip in one does not affect others.
        """
        return [self.process_file(r) for r in records]

    # ------------------------------------------------------------------
    # Stage 1 — Init
    # ------------------------------------------------------------------

    def _stage_init(
        self, record: FileRecord,
    ) -> tuple[bool, str | None]:
        """Verify the source file exists and record its size / mtime."""
        path = record.source_path
        if not os.path.exists(path):
            return False, f"file not found: {path}"

        record.file_size = FileSystemHelper.get_file_size(path)
        return True, None

    # ------------------------------------------------------------------
    # Stage 2 — Check processed
    # ------------------------------------------------------------------

    def _stage_check_processed(
        self, record: FileRecord,
    ) -> tuple[bool, str | None]:
        """Skip if an identical source_path was already completed in this session."""
        completed = self._db.get_file_records(
            self._session_id, status=FileStatus.COMPLETED,
        )
        for existing in completed:
            if (
                existing.source_path == record.source_path
                and existing.id != record.id
            ):
                return False, "already processed in this session"
        return True, None

    # ------------------------------------------------------------------
    # Stage 3 — Pattern skip
    # ------------------------------------------------------------------

    def _stage_pattern_skip(
        self, record: FileRecord,
    ) -> tuple[bool, str | None]:
        """Skip hidden / system files and files matching skip_filename_patterns."""
        if FileSystemHelper.is_hidden_or_system(record.source_path):
            return False, "hidden or system file"

        filename = os.path.basename(record.source_path)
        for pattern in self._skip_filename_patterns:
            if fnmatch.fnmatch(filename, pattern):
                return False, f"matched skip pattern: {pattern}"

        return True, None

    # ------------------------------------------------------------------
    # Stage 4 — Content detection
    # ------------------------------------------------------------------

    def _stage_detect(
        self, record: FileRecord,
    ) -> tuple[bool, str | None]:
        """Detect content type and file type using magic bytes / extension."""
        mime, ftype = self._detector.detect(record.source_path)
        record.content_type = mime
        record.file_type = ftype
        return True, None

    # ------------------------------------------------------------------
    # Stage 5 — Unknown filter
    # ------------------------------------------------------------------

    def _stage_hash(
        self, record: FileRecord,
    ) -> tuple[bool, str | None]:
        """Skip files with UNKNOWN file type."""
        if record.file_type == FileType.UNKNOWN:
            return False, "unknown file type"
        return True, None

    # ------------------------------------------------------------------
    # Stage 6 — SHA-256 hash + dedup check
    # ------------------------------------------------------------------

    def _stage_dedup(
        self, record: FileRecord,
    ) -> tuple[bool, str | None]:
        """Hash the file and check for exact-match duplicates."""
        record.sha256_hash = self._hasher.hash_file(record.source_path)

        dedup_result = self._dedup.check_duplicate(record, self._session_id)
        if dedup_result.is_duplicate:
            record.is_duplicate = True
            record.duplicate_group_id = dedup_result.duplicate_group_id
            return False, "exact duplicate"

        return True, None

    # ------------------------------------------------------------------
    # Stage 7 — Metadata extraction
    # ------------------------------------------------------------------

    def _stage_metadata(
        self, record: FileRecord,
    ) -> tuple[bool, str | None]:
        """Extract metadata based on file type."""
        if record.file_type == FileType.IMAGE:
            self._local.exif_result = self._exif_extractor.extract(
                record.source_path,
            )
            record.exif_status = self._local.exif_result.status
            record.exif_data = {
                "make": self._local.exif_result.make,
                "model": self._local.exif_result.model,
                "software": self._local.exif_result.software,
                "orientation": self._local.exif_result.orientation,
                "width": self._local.exif_result.width,
                "height": self._local.exif_result.height,
            }

        elif record.file_type == FileType.VIDEO:
            self._local.video_meta = self._video_extractor.extract(
                record.source_path,
            )

        elif record.file_type == FileType.AUDIO:
            self._local.audio_meta = self._audio_extractor.extract(
                record.source_path,
            )
            if self._musicbrainz_client is not None:
                self._local.audio_meta = self._musicbrainz_client.enrich(
                    self._local.audio_meta, record.source_path,
                )

        # DOCUMENT / SIDECAR: no metadata extraction needed.
        return True, None

    # ------------------------------------------------------------------
    # Stage 8 — Date resolution
    # ------------------------------------------------------------------

    def _stage_date_resolve(
        self, record: FileRecord,
    ) -> tuple[bool, str | None]:
        """Extract the best date from metadata + filename fallback."""
        exif_for_date = self._local.exif_result

        # For videos, build a lightweight ExifResult-like stand-in so
        # the date parser can use the video's creation date.
        if record.file_type == FileType.VIDEO and self._local.video_meta is not None:
            from sortique.engine.metadata.exif_extractor import ExifResult

            exif_for_date = ExifResult(
                date_original=self._local.video_meta.date,
                make=self._local.video_meta.make,
                model=self._local.video_meta.model,
            )

        self._local.date_result = self._date_parser.extract_date(
            record.source_path, exif_result=exif_for_date,
        )

        if self._local.date_result.date is not None:
            record.date_value = self._local.date_result.date
            record.date_source = self._local.date_result.source
            if self._local.date_result.timezone_offset:
                record.timezone_offset = self._local.date_result.timezone_offset

        return True, None

    # ------------------------------------------------------------------
    # Stage 9 — Categorise
    # ------------------------------------------------------------------

    def _stage_build_path(
        self, record: FileRecord,
    ) -> tuple[bool, str | None]:
        """Categorise the file and generate its destination path."""
        # --- categorise ---
        ext = os.path.splitext(record.source_path)[1]
        ext_lower = ext.lower().lstrip(".")

        if record.file_type == FileType.IMAGE:
            from sortique.engine.metadata.exif_extractor import ExifResult

            exif = self._local.exif_result or ExifResult()
            record.category = self._categorizer.categorize_image(
                record.source_path, exif, ext_lower,
            )
        elif record.file_type == FileType.VIDEO:
            from sortique.engine.metadata.video_metadata import VideoMetadata

            vmeta = self._local.video_meta or VideoMetadata(duration_unknown=True)
            record.category = self._categorizer.categorize_video(
                record.source_path, vmeta,
            )
        elif record.file_type == FileType.AUDIO:
            from sortique.engine.metadata.audio_metadata import AudioMetadata

            ameta = self._local.audio_meta or AudioMetadata()
            record.category = self._categorizer.categorize_audio(
                record.source_path, ameta,
            )
        elif record.file_type == FileType.DOCUMENT:
            record.category = self._categorizer.categorize_document(
                record.source_path,
            )
        else:
            record.category = "Other"

        # --- destination path ---
        stem = os.path.splitext(os.path.basename(record.source_path))[0]

        # For video files, build a lightweight ExifResult so the path generator
        # can place "Camera" recordings into per-device make/model sub-folders.
        path_exif = self._local.exif_result
        if (
            record.file_type == FileType.VIDEO
            and self._local.video_meta is not None
            and (
                self._local.video_meta.make is not None
                or self._local.video_meta.model is not None
            )
        ):
            from sortique.engine.metadata.exif_extractor import ExifResult

            path_exif = ExifResult(
                make=self._local.video_meta.make,
                model=self._local.video_meta.model,
            )

        dest = self._path_gen.generate(
            category=record.category,
            original_filename=stem,
            original_ext=ext,
            date_result=self._local.date_result,
            exif=path_exif,
            file_type=record.file_type,
            content_type=record.content_type,
            source_path=record.source_path,
        )
        record.destination_path = dest

        return True, None

    # ------------------------------------------------------------------
    # Stage 10 — Conflict resolution
    # ------------------------------------------------------------------

    def _stage_copy(
        self, record: FileRecord,
    ) -> tuple[bool, str | None]:
        """Resolve filename conflicts and perform the file copy."""
        if record.destination_path is None:
            return False, "no destination path"

        # Resolve conflicts (appends -1, -2, … if needed).
        record.destination_path = self._path_gen.resolve_conflict(
            record.destination_path,
        )

        # --- file operation ---
        if self._dry_run:
            return True, None

        dest_dir = os.path.dirname(record.destination_path)
        os.makedirs(dest_dir, exist_ok=True)

        if record.file_type == FileType.IMAGE:
            self._image_processor.copy_original(
                record.source_path, record.destination_path,
            )
            # Generate resized JPEG/PNG export for Originals and RAW only.
            if record.category in ("Originals", "RAW"):
                self._generate_image_export(record)
        elif record.file_type == FileType.VIDEO:
            stem = os.path.splitext(
                os.path.basename(record.destination_path),
            )[0]
            self._video_processor.copy_with_sidecars(
                record.source_path, dest_dir, stem,
            )
        elif record.file_type == FileType.AUDIO:
            self._audio_processor.process(
                record.source_path, record.destination_path,
                audio_metadata=self._local.audio_meta,
            )
        elif record.file_type == FileType.DOCUMENT:
            self._document_processor.process(
                record.source_path, record.destination_path,
            )
        else:
            FileSystemHelper.atomic_copy(
                record.source_path, record.destination_path,
            )

        return True, None

    # ------------------------------------------------------------------
    # Export copy generation (Originals / RAW images only)
    # ------------------------------------------------------------------

    def _generate_image_export(self, record: FileRecord) -> None:
        """Generate a resized JPEG/PNG export copy alongside the original."""
        stem = os.path.splitext(os.path.basename(record.source_path))[0]
        ext = os.path.splitext(record.source_path)[1]

        export_dest = self._path_gen.generate(
            category=record.category,
            original_filename=stem,
            original_ext=ext,
            date_result=self._local.date_result,
            exif=self._local.exif_result,
            file_type=record.file_type,
            is_export=True,
        )
        export_dest = self._path_gen.resolve_conflict(export_dest)

        try:
            self._image_processor.generate_export(
                record.source_path, export_dest, self._local.exif_result,
            )
        except Exception as exc:
            logger.warning(
                "Export generation failed for %s: %s",
                record.source_path, exc,
            )

    # ------------------------------------------------------------------
    # Stage 11 — RAW+JPEG pairing (placeholder — handled at batch level)
    # ------------------------------------------------------------------

    def _stage_pair(
        self, record: FileRecord,
    ) -> tuple[bool, str | None]:
        """RAW+JPEG pairing is handled after the full batch completes."""
        return True, None

    # ------------------------------------------------------------------
    # Stage 12 — DB update
    # ------------------------------------------------------------------

    def _stage_cleanup(
        self, record: FileRecord,
    ) -> tuple[bool, str | None]:
        """Persist the fully populated file record to the database."""
        if not self._dry_run:
            self._db.update_file_record(record)

            # Record in portable manifest for cross-machine dedup.
            if record.sha256_hash and record.destination_path:
                rel = os.path.relpath(
                    record.destination_path,
                    self._path_gen.destination_root,
                )
                self._dedup.record_in_manifest(
                    record.sha256_hash, rel, record.file_size,
                )

        return True, None

    # ------------------------------------------------------------------
    # Stage 13 — Progress / verify
    # ------------------------------------------------------------------

    def _stage_verify(
        self, record: FileRecord,
    ) -> tuple[bool, str | None]:
        """Emit progress and optionally verify the copy."""
        if (
            not self._dry_run
            and record.destination_path
            and os.path.exists(record.destination_path)
        ):
            record.verified = True
        logger.debug(
            "Processed %s → %s [%s]",
            record.source_path,
            record.destination_path,
            record.category,
        )
        return True, None
