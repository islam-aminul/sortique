"""Burst photo sequence detection using layered criteria."""

from __future__ import annotations

import fnmatch
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sortique.data.config_manager import ConfigManager
    from sortique.engine.metadata.date_parser import DateResult
    from sortique.engine.metadata.exif_extractor import ExifResult


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class BurstGroup:
    """A group of photos identified as a burst sequence."""

    files: list[str]            # file paths in sequence order
    date: datetime
    camera: str | None          # "Make - Model" or ``None``
    sequence_start: int         # starting index for -NNN numbering


# ---------------------------------------------------------------------------
# Regex for extracting the stem prefix before a burst/bracket keyword
# ---------------------------------------------------------------------------

# Matches common burst keyword variants embedded in filenames.
# Captures everything before the keyword as group "prefix".
_BURST_KEYWORD_RE = re.compile(
    r"^(?P<prefix>.+?)[_\-](?:BURST|BRACKETED|burst|bracketed)\d*",
)


# ---------------------------------------------------------------------------
# BurstDetector
# ---------------------------------------------------------------------------

class BurstDetector:
    """Detects burst photo sequences using layered criteria.

    Detection is applied in order of specificity:

    1. **EXIF burst mode** — MakerNote BurstMode / SequenceNumber tags.
    2. **Filename patterns** — files matching ``burst_filename_patterns``
       config (glob-style) are grouped by shared stem prefix.
    3. **Timestamp grouping** — ≥ 3 images from the same camera with
       identical timestamps (same second) and sequential filenames.

    A file can only belong to **one** burst group.
    """

    def __init__(self, config: ConfigManager) -> None:
        self.config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect_bursts(
        self,
        file_records: list[tuple[str, ExifResult, DateResult]],
    ) -> list[BurstGroup]:
        """Detect burst sequences from a collection of image files.

        *file_records* is a list of ``(filepath, ExifResult, DateResult)``
        tuples.

        Returns a list of :class:`BurstGroup`.  Each file appears in at
        most one group.
        """
        claimed: set[str] = set()
        groups: list[BurstGroup] = []

        # --- Layer 1: EXIF burst tags ---
        exif_groups = self._group_by_exif_burst(file_records)
        for g in exif_groups:
            groups.append(g)
            claimed.update(g.files)

        # --- Layer 2: filename patterns ---
        remaining = [r for r in file_records if r[0] not in claimed]
        pattern_groups = self._group_by_filename_pattern(remaining)
        for g in pattern_groups:
            groups.append(g)
            claimed.update(g.files)

        # --- Layer 3: timestamp grouping ---
        remaining = [r for r in file_records if r[0] not in claimed]
        ts_groups = self._group_by_timestamp(remaining)
        for g in ts_groups:
            groups.append(g)
            claimed.update(g.files)

        return groups

    # ------------------------------------------------------------------
    # Layer 1: EXIF burst mode
    # ------------------------------------------------------------------

    def _check_exif_burst(self, filepath: str, exif: ExifResult) -> bool:
        """Return ``True`` when EXIF data indicates burst mode.

        Checks for common MakerNote burst indicators stored in
        ``exif.exif_data`` (the raw tag dict carried by some extractors):

        * ``BurstMode`` ≠ 0
        * Presence of ``SequenceNumber``
        """
        if exif is None:
            return False

        raw = getattr(exif, "exif_data", None)
        if not isinstance(raw, dict):
            return False

        # Some cameras expose BurstMode as a top-level maker note key.
        burst_mode = raw.get("BurstMode")
        if burst_mode is not None and burst_mode != 0:
            return True

        if "SequenceNumber" in raw:
            return True

        return False

    def _group_by_exif_burst(
        self,
        files: list[tuple[str, ExifResult, DateResult]],
    ) -> list[BurstGroup]:
        """Group files whose EXIF tags indicate burst mode.

        Files are grouped by ``(camera, timestamp_second)``.
        """
        burst_files: list[tuple[str, ExifResult, DateResult]] = [
            (fp, exif, dr)
            for fp, exif, dr in files
            if self._check_exif_burst(fp, exif)
        ]

        if not burst_files:
            return []

        # Group by (camera, timestamp truncated to second).
        buckets: dict[tuple[str | None, datetime | None], list[tuple[str, ExifResult, DateResult]]] = defaultdict(list)
        for fp, exif, dr in burst_files:
            cam = _camera_key(exif)
            ts = _truncate_second(dr)
            buckets[(cam, ts)].append((fp, exif, dr))

        groups: list[BurstGroup] = []
        for (cam, ts), members in buckets.items():
            if len(members) < 2:
                continue
            members.sort(key=lambda r: os.path.basename(r[0]).lower())
            groups.append(BurstGroup(
                files=[m[0] for m in members],
                date=ts or members[0][2].date or datetime.min,
                camera=cam,
                sequence_start=0,
            ))
        return groups

    # ------------------------------------------------------------------
    # Layer 2: filename patterns
    # ------------------------------------------------------------------

    def _group_by_filename_pattern(
        self,
        files: list[tuple[str, ExifResult, DateResult]],
    ) -> list[BurstGroup]:
        """Group files whose filename matches ``burst_filename_patterns``.

        Files matching the pattern are grouped by the shared stem prefix
        before the burst keyword (e.g. ``IMG_20240315_143000`` from
        ``IMG_20240315_143000_BURST001.jpg``).
        """
        patterns: list[str] = self.config.get("burst_filename_patterns", [])
        if not patterns:
            return []

        # Collect files that match any burst filename pattern.
        matched: list[tuple[str, ExifResult, DateResult, str]] = []  # (…, prefix)
        for fp, exif, dr in files:
            name = os.path.basename(fp)
            stem = os.path.splitext(name)[0]

            if not any(fnmatch.fnmatch(stem, pat) for pat in patterns):
                continue

            # Extract prefix before the burst keyword.
            m = _BURST_KEYWORD_RE.match(stem)
            prefix = m.group("prefix") if m else stem
            matched.append((fp, exif, dr, prefix))

        if not matched:
            return []

        # Group by (prefix, camera).
        buckets: dict[tuple[str, str | None], list[tuple[str, ExifResult, DateResult]]] = defaultdict(list)
        for fp, exif, dr, prefix in matched:
            cam = _camera_key(exif)
            buckets[(prefix.lower(), cam)].append((fp, exif, dr))

        groups: list[BurstGroup] = []
        for (prefix, cam), members in buckets.items():
            if len(members) < 2:
                continue
            members.sort(key=lambda r: os.path.basename(r[0]).lower())
            date = _best_date(members)
            groups.append(BurstGroup(
                files=[m[0] for m in members],
                date=date,
                camera=cam,
                sequence_start=0,
            ))
        return groups

    # ------------------------------------------------------------------
    # Layer 3: timestamp grouping
    # ------------------------------------------------------------------

    def _group_by_timestamp(
        self,
        files: list[tuple[str, ExifResult, DateResult]],
    ) -> list[BurstGroup]:
        """Group images with identical per-second timestamps from the same camera.

        Requires **≥ 3** files per group.
        """
        # Only files with a resolved date participate.
        dated = [
            (fp, exif, dr)
            for fp, exif, dr in files
            if dr and dr.date is not None
        ]

        buckets: dict[tuple[str | None, datetime], list[tuple[str, ExifResult, DateResult]]] = defaultdict(list)
        for fp, exif, dr in dated:
            cam = _camera_key(exif)
            ts = dr.date.replace(microsecond=0)
            buckets[(cam, ts)].append((fp, exif, dr))

        groups: list[BurstGroup] = []
        for (cam, ts), members in buckets.items():
            if len(members) < 3:
                continue
            members.sort(key=lambda r: os.path.basename(r[0]).lower())
            groups.append(BurstGroup(
                files=[m[0] for m in members],
                date=ts,
                camera=cam,
                sequence_start=0,
            ))
        return groups


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _camera_key(exif: ExifResult | None) -> str | None:
    """Build a ``"Make - Model"`` string, or ``None``."""
    if exif is None:
        return None
    make = (exif.make or "").strip()
    model = (exif.model or "").strip()
    if not make and not model:
        return None
    if make and model:
        if model.lower().startswith(make.lower()):
            return model
        return f"{make} - {model}"
    return make or model


def _truncate_second(dr: DateResult | None) -> datetime | None:
    if dr is None or dr.date is None:
        return None
    return dr.date.replace(microsecond=0)


def _best_date(
    members: list[tuple[str, ExifResult, DateResult]],
) -> datetime:
    """Return the best available date from a group of records."""
    for _, _, dr in members:
        if dr and dr.date is not None:
            return dr.date
    return datetime.min
