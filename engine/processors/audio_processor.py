"""Audio processing: copy with optional metadata enrichment."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import TYPE_CHECKING

import mutagen
from mutagen.id3 import ID3, TALB, TCON, TIT2, TPE1
from mutagen.mp4 import MP4
from mutagen.flac import FLAC
from mutagen.oggvorbis import OggVorbis
from mutagen.oggopus import OggOpus

from sortique.data.file_system import FileSystemHelper
from sortique.engine.processors import ProcessResult

if TYPE_CHECKING:
    from sortique.data.config_manager import ConfigManager
    from sortique.engine.metadata.audio_metadata import AudioMetadata

logger = logging.getLogger(__name__)


class AudioProcessor:
    """Audio processing: copy with optional metadata enrichment.

    Audio files are copied to destination, and optionally enriched
    with metadata tags when enabled.
    """

    def __init__(self, config: ConfigManager) -> None:
        self.config = config
        # Check if metadata writing is enabled (default: True)
        self.write_metadata = config.get("write_audio_metadata", True)

    def process(
        self,
        source: str,
        destination: str,
        progress_callback: Callable[[int, int], None] | None = None,
        audio_metadata: AudioMetadata | None = None,
    ) -> ProcessResult:
        """Copy *source* audio file to *destination* and optionally write metadata.
        
        Parameters
        ----------
        source:
            Source audio file path.
        destination:
            Destination audio file path.
        progress_callback:
            Optional progress callback.
        audio_metadata:
            Optional enriched metadata to write to the destination file.
        """
        try:
            file_size = os.path.getsize(source)
            FileSystemHelper.atomic_copy(source, destination, progress_callback)
            
            # Write enriched metadata if enabled and metadata is provided
            if self.write_metadata and audio_metadata is not None:
                self._write_metadata_tags(destination, audio_metadata)
            
            return ProcessResult(
                success=True,
                source_path=source,
                dest_path=destination,
                bytes_copied=file_size,
                is_sidecar=False,
                error=None,
            )
        except Exception as exc:
            return ProcessResult(
                success=False,
                source_path=source,
                dest_path=destination,
                bytes_copied=0,
                is_sidecar=False,
                error=str(exc),
            )

    def _write_metadata_tags(self, filepath: str, metadata: AudioMetadata) -> None:
        """Write metadata tags to audio file.
        
        Supports MP3 (ID3), MP4/M4A, FLAC, OGG, and OPUS formats.
        Never raises - logs warnings on failure.
        """
        try:
            audio = mutagen.File(filepath)
            if audio is None:
                logger.warning(f"Could not write metadata to {filepath}: unsupported format")
                return
            
            cls_name = type(audio).__name__
            
            # MP3 with ID3 tags
            if cls_name in ("MP3", "AIFF"):
                self._write_id3_tags(audio, metadata)
            
            # MP4/M4A/AAC
            elif cls_name in ("MP4", "M4A", "AAC"):
                self._write_mp4_tags(audio, metadata)
            
            # FLAC
            elif cls_name == "FLAC":
                self._write_vorbis_tags(audio, metadata)
            
            # OGG Vorbis
            elif cls_name == "OggVorbis":
                self._write_vorbis_tags(audio, metadata)
            
            # OGG Opus
            elif cls_name == "OggOpus":
                self._write_vorbis_tags(audio, metadata)
            
            else:
                logger.debug(f"Metadata writing not supported for {cls_name}: {filepath}")
                return
            
            audio.save()
            logger.debug(f"Wrote metadata to {filepath}")
            
        except Exception as exc:
            logger.warning(f"Failed to write metadata to {filepath}: {exc}")

    def _write_id3_tags(self, audio: mutagen.FileType, metadata: AudioMetadata) -> None:
        """Write ID3 tags for MP3 files."""
        from mutagen.id3 import TDRC
        
        # Ensure ID3 tags exist
        if audio.tags is None:
            audio.add_tags()
        
        tags = audio.tags
        
        # Write title
        if metadata.title:
            tags.delall("TIT2")  # Remove existing title tags
            tags.add(TIT2(encoding=3, text=[metadata.title]))
        
        # Write artist
        if metadata.artist:
            tags.delall("TPE1")  # Remove existing artist tags
            tags.add(TPE1(encoding=3, text=[metadata.artist]))
        
        # Write album
        if metadata.album:
            tags.delall("TALB")  # Remove existing album tags
            tags.add(TALB(encoding=3, text=[metadata.album]))
        
        # Write genre
        if metadata.genre:
            tags.delall("TCON")  # Remove existing genre tags
            tags.add(TCON(encoding=3, text=[metadata.genre]))
        
        # Write year
        if metadata.year:
            tags.delall("TDRC")  # Remove existing date tags
            tags.add(TDRC(encoding=3, text=[str(metadata.year)]))

    def _write_mp4_tags(self, audio: mutagen.FileType, metadata: AudioMetadata) -> None:
        """Write MP4/M4A tags."""
        tags = audio.tags
        if tags is None:
            audio.add_tags()
            tags = audio.tags
        
        # Write title
        if metadata.title:
            tags["\xa9nam"] = [metadata.title]
        
        # Write artist
        if metadata.artist:
            tags["\xa9ART"] = [metadata.artist]
        
        # Write album
        if metadata.album:
            tags["\xa9alb"] = [metadata.album]
        
        # Write genre
        if metadata.genre:
            tags["\xa9gen"] = [metadata.genre]
        
        # Write year
        if metadata.year:
            tags["\xa9day"] = [str(metadata.year)]

    def _write_vorbis_tags(self, audio: mutagen.FileType, metadata: AudioMetadata) -> None:
        """Write Vorbis Comment tags for FLAC, OGG, OPUS."""
        tags = audio.tags
        if tags is None:
            audio.add_tags()
            tags = audio.tags
        
        # Write title
        if metadata.title:
            tags["title"] = metadata.title
        
        # Write artist
        if metadata.artist:
            tags["artist"] = metadata.artist
        
        # Write album
        if metadata.album:
            tags["album"] = metadata.album
        
        # Write genre
        if metadata.genre:
            tags["genre"] = metadata.genre
        
        # Write year
        if metadata.year:
            tags["date"] = str(metadata.year)
