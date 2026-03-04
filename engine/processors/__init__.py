"""Sortique file processors."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ProcessResult:
    """Outcome of a single file copy / process operation."""

    success: bool
    source_path: str
    dest_path: str
    bytes_copied: int
    is_sidecar: bool
    error: str | None
