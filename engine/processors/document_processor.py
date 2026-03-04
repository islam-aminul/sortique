"""Document processing: copy-only with size extraction."""

from __future__ import annotations

import os
from collections.abc import Callable

from sortique.data.file_system import FileSystemHelper
from sortique.engine.processors import ProcessResult


class DocumentProcessor:
    """Document processing: copy-only with size extraction.

    Documents are never modified.  The processor copies the original and
    records the byte count for reporting.
    """

    def process(
        self,
        source: str,
        destination: str,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> ProcessResult:
        """Copy *source* document to *destination* using atomic copy.

        Extracts ``file_size`` for reporting.
        """
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
