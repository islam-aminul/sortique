"""Audio processing: copy-only."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import TYPE_CHECKING

from sortique.data.file_system import FileSystemHelper
from sortique.engine.processors import ProcessResult

if TYPE_CHECKING:
    from sortique.data.config_manager import ConfigManager


class AudioProcessor:
    """Audio processing: copy-only.

    Audio files are never re-encoded or modified.
    """

    def __init__(self, config: ConfigManager) -> None:
        self.config = config

    def process(
        self,
        source: str,
        destination: str,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> ProcessResult:
        """Copy *source* audio file to *destination* using atomic copy."""
        try:
            file_size = os.path.getsize(source)
            FileSystemHelper.atomic_copy(source, destination, progress_callback)
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
