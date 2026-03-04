"""Application factory — creates and wires all Sortique dependencies."""

from __future__ import annotations

import os
from typing import Any

from sortique.data.config_manager import ConfigManager
from sortique.data.database import Database
from sortique.data.file_system import FileSystemHelper
from sortique.data.lock_manager import LockManager
from sortique.engine.burst_detector import BurstDetector
from sortique.engine.categorizer import Categorizer
from sortique.engine.dedup import DedupEngine
from sortique.engine.detector import ContentDetector
from sortique.engine.hasher import FileHasher
from sortique.engine.metadata.audio_metadata import AudioMetadataExtractor
from sortique.engine.metadata.date_parser import DateParser
from sortique.engine.metadata.exif_extractor import ExifExtractor
from sortique.engine.metadata.musicbrainz_client import MusicBrainzClient
from sortique.engine.metadata.video_metadata import VideoMetadataExtractor
from sortique.engine.pair_detector import PairDetector
from sortique.engine.path_generator import PathGenerator
from sortique.engine.processors.audio_processor import AudioProcessor
from sortique.engine.processors.document_processor import DocumentProcessor
from sortique.engine.processors.image_processor import ImageProcessor
from sortique.engine.processors.video_processor import VideoProcessor
from sortique.engine.scanner import Scanner
from sortique.service.collection_review import CollectionReviewer
from sortique.service.dry_run import DryRunManager
from sortique.service.notification_service import NotificationService
from sortique.service.pipeline import Pipeline
from sortique.service.session_manager import SessionManager
from sortique.service.space_checker import SpaceChecker
from sortique.service.thread_pool import FileProcessorPool
from sortique.service.undo_manager import UndoManager


class AppFactory:
    """Creates and wires all application dependencies.

    Single source of truth for dependency initialisation.  Stateless
    singletons are cached; stateful or parameterised objects (Pipeline,
    PathGenerator, etc.) are created fresh each time.
    """

    def __init__(
        self,
        config_dir: str | None = None,
        db_path: str | None = None,
    ) -> None:
        self.config = ConfigManager(config_dir)
        self.db = Database(db_path or self._default_db_path())
        self._instances: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Default paths
    # ------------------------------------------------------------------

    @staticmethod
    def _default_db_path() -> str:
        """``~/.sortique/sortique.db``"""
        base = os.path.join(os.path.expanduser("~"), ".sortique")
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "sortique.db")

    # ------------------------------------------------------------------
    # Singleton helper
    # ------------------------------------------------------------------

    def _singleton(self, key: str, factory):
        """Return a cached instance, creating it on first access."""
        if key not in self._instances:
            self._instances[key] = factory()
        return self._instances[key]

    # ------------------------------------------------------------------
    # Data layer
    # ------------------------------------------------------------------

    def lock_manager(self, destination_dir: str) -> LockManager:
        return LockManager(destination_dir)

    def file_system(self) -> FileSystemHelper:
        return self._singleton("file_system", FileSystemHelper)

    # ------------------------------------------------------------------
    # Engine layer
    # ------------------------------------------------------------------

    def scanner(self) -> Scanner:
        return self._singleton("scanner", lambda: Scanner(self.config))

    def detector(self) -> ContentDetector:
        return self._singleton("detector", ContentDetector)

    def hasher(self) -> FileHasher:
        return self._singleton("hasher", FileHasher)

    def dedup_engine(self) -> DedupEngine:
        return self._singleton(
            "dedup_engine",
            lambda: DedupEngine(self.db, self.hasher()),
        )

    def exif_extractor(self) -> ExifExtractor:
        return self._singleton("exif_extractor", ExifExtractor)

    def date_parser(self) -> DateParser:
        return self._singleton(
            "date_parser", lambda: DateParser(self.config),
        )

    def video_extractor(self) -> VideoMetadataExtractor:
        return self._singleton("video_extractor", VideoMetadataExtractor)

    def audio_extractor(self) -> AudioMetadataExtractor:
        return self._singleton("audio_extractor", AudioMetadataExtractor)

    def categorizer(self) -> Categorizer:
        return self._singleton(
            "categorizer", lambda: Categorizer(self.config),
        )

    def path_generator(self, destination_dir: str) -> PathGenerator:
        # New instance per destination_dir — not cached.
        return PathGenerator(self.config, destination_dir)

    def image_processor(self) -> ImageProcessor:
        return self._singleton(
            "image_processor", lambda: ImageProcessor(self.config),
        )

    def video_processor(self) -> VideoProcessor:
        return self._singleton(
            "video_processor", lambda: VideoProcessor(self.config),
        )

    def audio_processor(self) -> AudioProcessor:
        return self._singleton(
            "audio_processor", lambda: AudioProcessor(self.config),
        )

    def document_processor(self) -> DocumentProcessor:
        return self._singleton("document_processor", DocumentProcessor)

    def burst_detector(self) -> BurstDetector:
        return self._singleton(
            "burst_detector", lambda: BurstDetector(self.config),
        )

    def pair_detector(self) -> PairDetector:
        return self._singleton("pair_detector", PairDetector)

    def musicbrainz_client(self) -> MusicBrainzClient:
        return self._singleton(
            "musicbrainz_client",
            lambda: MusicBrainzClient(enabled=self.config.musicbrainz_enabled),
        )

    # ------------------------------------------------------------------
    # Service layer
    # ------------------------------------------------------------------

    def pipeline(
        self,
        destination_dir: str,
        dry_run: bool = False,
    ) -> Pipeline:
        """Create a Pipeline with ALL engine dependencies wired.

        Always returns a new instance (stateful per-session).
        """
        session_mgr = self.session_manager()
        # session_id is set by the caller after creating a session.
        # We need a session_id to construct Pipeline, but the factory
        # creates an un-bound pipeline — callers wire session_id.
        # For now, provide an empty placeholder; callers must create
        # Pipeline directly when they have a session_id.
        return Pipeline(
            db=self.db,
            session_id="",  # caller sets this
            detector=self.detector(),
            hasher=self.hasher(),
            dedup=self.dedup_engine(),
            categorizer=self.categorizer(),
            path_generator=self.path_generator(destination_dir),
            exif_extractor=self.exif_extractor(),
            date_parser=self.date_parser(),
            video_extractor=self.video_extractor(),
            audio_extractor=self.audio_extractor(),
            image_processor=self.image_processor(),
            video_processor=self.video_processor(),
            audio_processor=self.audio_processor(),
            document_processor=self.document_processor(),
            dry_run=dry_run,
        )

    def session_manager(self) -> SessionManager:
        return self._singleton(
            "session_manager",
            lambda: SessionManager(self.db, self.config),
        )

    def space_checker(self) -> SpaceChecker:
        return self._singleton("space_checker", SpaceChecker)

    def thread_pool(self, destination_dir: str) -> FileProcessorPool:
        """Create a thread pool with a fresh Pipeline.  New instance each call."""
        pipe = self.pipeline(destination_dir)
        return FileProcessorPool(
            pipe, self.db, num_workers=self.config.threads,
        )

    def dry_run_manager(self, destination_dir: str) -> DryRunManager:
        """Create a DryRunManager with a dry-run Pipeline."""
        pipe = self.pipeline(destination_dir, dry_run=True)
        return DryRunManager(
            pipe, self.space_checker(), self.pair_detector(), self.db,
        )

    def undo_manager(self) -> UndoManager:
        return self._singleton(
            "undo_manager",
            lambda: UndoManager(self.db, self.session_manager()),
        )

    def collection_reviewer(self, destination_dir: str) -> CollectionReviewer:
        return CollectionReviewer(
            self.db,
            self.categorizer(),
            self.exif_extractor(),
            self.date_parser(),
            self.path_generator(destination_dir),
        )

    def notification_service(self) -> NotificationService:
        return self._singleton("notification_service", NotificationService)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Clean up: close database, release resources."""
        self.db.close()
        self._instances.clear()
