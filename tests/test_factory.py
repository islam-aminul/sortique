"""Smoke tests for AppFactory — verify all dependencies wire correctly."""

from __future__ import annotations

import os

import pytest

from sortique.factory import AppFactory

# Engine classes
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

# Data layer
from sortique.data.file_system import FileSystemHelper
from sortique.data.lock_manager import LockManager

# Service layer
from sortique.service.collection_review import CollectionReviewer
from sortique.service.dry_run import DryRunManager
from sortique.service.notification_service import NotificationService
from sortique.service.pipeline import Pipeline
from sortique.service.session_manager import SessionManager
from sortique.service.space_checker import SpaceChecker
from sortique.service.thread_pool import FileProcessorPool
from sortique.service.undo_manager import UndoManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def factory(tmp_path):
    """Create an AppFactory with temp paths so nothing touches the real home dir."""
    config_dir = str(tmp_path / "config")
    db_path = str(tmp_path / "test.db")
    f = AppFactory(config_dir=config_dir, db_path=db_path)
    yield f
    f.close()


# ---------------------------------------------------------------------------
# All dependencies create without error
# ---------------------------------------------------------------------------

class TestFactoryCreatesAll:
    """Verify every factory method returns the correct type."""

    # -- Data layer --

    def test_config(self, factory):
        assert factory.config is not None

    def test_db(self, factory):
        assert factory.db is not None

    def test_lock_manager(self, factory, tmp_path):
        lm = factory.lock_manager(str(tmp_path))
        assert isinstance(lm, LockManager)

    def test_file_system(self, factory):
        assert isinstance(factory.file_system(), FileSystemHelper)

    # -- Engine layer --

    def test_scanner(self, factory):
        assert isinstance(factory.scanner(), Scanner)

    def test_detector(self, factory):
        assert isinstance(factory.detector(), ContentDetector)

    def test_hasher(self, factory):
        assert isinstance(factory.hasher(), FileHasher)

    def test_dedup_engine(self, factory):
        assert isinstance(factory.dedup_engine(), DedupEngine)

    def test_exif_extractor(self, factory):
        assert isinstance(factory.exif_extractor(), ExifExtractor)

    def test_date_parser(self, factory):
        assert isinstance(factory.date_parser(), DateParser)

    def test_video_extractor(self, factory):
        assert isinstance(factory.video_extractor(), VideoMetadataExtractor)

    def test_audio_extractor(self, factory):
        assert isinstance(factory.audio_extractor(), AudioMetadataExtractor)

    def test_categorizer(self, factory):
        assert isinstance(factory.categorizer(), Categorizer)

    def test_path_generator(self, factory, tmp_path):
        pg = factory.path_generator(str(tmp_path))
        assert isinstance(pg, PathGenerator)

    def test_image_processor(self, factory):
        assert isinstance(factory.image_processor(), ImageProcessor)

    def test_video_processor(self, factory):
        assert isinstance(factory.video_processor(), VideoProcessor)

    def test_audio_processor(self, factory):
        assert isinstance(factory.audio_processor(), AudioProcessor)

    def test_document_processor(self, factory):
        assert isinstance(factory.document_processor(), DocumentProcessor)

    def test_burst_detector(self, factory):
        assert isinstance(factory.burst_detector(), BurstDetector)

    def test_pair_detector(self, factory):
        assert isinstance(factory.pair_detector(), PairDetector)

    def test_musicbrainz_client(self, factory):
        assert isinstance(factory.musicbrainz_client(), MusicBrainzClient)

    # -- Service layer --

    def test_pipeline(self, factory, tmp_path):
        pipe = factory.pipeline(str(tmp_path))
        assert isinstance(pipe, Pipeline)

    def test_pipeline_dry_run(self, factory, tmp_path):
        pipe = factory.pipeline(str(tmp_path), dry_run=True)
        assert isinstance(pipe, Pipeline)
        assert pipe._dry_run is True

    def test_session_manager(self, factory):
        assert isinstance(factory.session_manager(), SessionManager)

    def test_space_checker(self, factory):
        assert isinstance(factory.space_checker(), SpaceChecker)

    def test_thread_pool(self, factory, tmp_path):
        pool = factory.thread_pool(str(tmp_path))
        assert isinstance(pool, FileProcessorPool)

    def test_dry_run_manager(self, factory, tmp_path):
        drm = factory.dry_run_manager(str(tmp_path))
        assert isinstance(drm, DryRunManager)

    def test_undo_manager(self, factory):
        assert isinstance(factory.undo_manager(), UndoManager)

    def test_collection_reviewer(self, factory, tmp_path):
        cr = factory.collection_reviewer(str(tmp_path))
        assert isinstance(cr, CollectionReviewer)

    def test_notification_service(self, factory):
        assert isinstance(factory.notification_service(), NotificationService)


# ---------------------------------------------------------------------------
# Singleton caching
# ---------------------------------------------------------------------------

class TestSingletonCaching:
    """Stateless services should return the same instance on repeated calls."""

    def test_detector_is_cached(self, factory):
        assert factory.detector() is factory.detector()

    def test_hasher_is_cached(self, factory):
        assert factory.hasher() is factory.hasher()

    def test_session_manager_is_cached(self, factory):
        assert factory.session_manager() is factory.session_manager()

    def test_categorizer_is_cached(self, factory):
        assert factory.categorizer() is factory.categorizer()

    def test_space_checker_is_cached(self, factory):
        assert factory.space_checker() is factory.space_checker()

    def test_pipeline_is_not_cached(self, factory, tmp_path):
        """Pipeline is stateful — each call returns a new instance."""
        p1 = factory.pipeline(str(tmp_path))
        p2 = factory.pipeline(str(tmp_path))
        assert p1 is not p2

    def test_path_generator_is_not_cached(self, factory, tmp_path):
        pg1 = factory.path_generator(str(tmp_path))
        pg2 = factory.path_generator(str(tmp_path))
        assert pg1 is not pg2


# ---------------------------------------------------------------------------
# Close cleans up
# ---------------------------------------------------------------------------

class TestClose:
    def test_close_clears_cache(self, tmp_path):
        config_dir = str(tmp_path / "config")
        db_path = str(tmp_path / "test.db")
        factory = AppFactory(config_dir=config_dir, db_path=db_path)

        # Prime some singletons.
        factory.detector()
        factory.hasher()
        assert len(factory._instances) > 0

        factory.close()
        assert len(factory._instances) == 0

    def test_close_is_safe_to_call_twice(self, tmp_path):
        config_dir = str(tmp_path / "config")
        db_path = str(tmp_path / "test.db")
        factory = AppFactory(config_dir=config_dir, db_path=db_path)
        factory.close()
        # Second close should not raise.
        factory.close()

    def test_db_file_created(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        factory = AppFactory(
            config_dir=str(tmp_path / "config"),
            db_path=db_path,
        )
        assert os.path.exists(db_path)
        factory.close()
