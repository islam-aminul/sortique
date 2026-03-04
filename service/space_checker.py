"""Pre-flight disk-space verification before file operations."""

from __future__ import annotations

import math
from dataclasses import dataclass

from sortique.constants import SPACE_BUFFER_FACTOR, SPACE_OVERHEAD_FACTOR
from sortique.data.file_system import FileSystemHelper


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class SpaceCheckResult:
    """Outcome of a pre-flight space check."""

    required_bytes: int
    available_bytes: int
    passes: bool
    shortfall_bytes: int  # 0 when *passes* is True


# ---------------------------------------------------------------------------
# Checker
# ---------------------------------------------------------------------------

class SpaceChecker:
    """Pre-flight space verification before file operations.

    The required space is computed as::

        required = total_source_bytes * SPACE_OVERHEAD_FACTOR * SPACE_BUFFER_FACTOR

    where ``SPACE_OVERHEAD_FACTOR`` (1.3) accounts for potential JPEG expansion
    and ``SPACE_BUFFER_FACTOR`` (1.1) adds an extra safety margin — giving a
    combined multiplier of ~1.43x.
    """

    def check(
        self,
        total_source_bytes: int,
        destination_dir: str,
    ) -> SpaceCheckResult:
        """Calculate required space and verify the destination can accommodate.

        Parameters
        ----------
        total_source_bytes:
            Sum of all source file sizes in bytes.
        destination_dir:
            Path whose filesystem will be queried for free space.

        Returns
        -------
        SpaceCheckResult
            Contains *required_bytes*, *available_bytes*, whether the check
            *passes*, and the *shortfall_bytes* (``0`` when passing).
        """
        required = math.ceil(
            total_source_bytes * SPACE_OVERHEAD_FACTOR * SPACE_BUFFER_FACTOR
        )
        available = FileSystemHelper.get_free_space(destination_dir)
        passes = available >= required
        shortfall = 0 if passes else required - available

        return SpaceCheckResult(
            required_bytes=required,
            available_bytes=available,
            passes=passes,
            shortfall_bytes=shortfall,
        )
