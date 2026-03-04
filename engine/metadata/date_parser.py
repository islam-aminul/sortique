"""Multi-source date extraction with fallback chain."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from sortique.constants import DateSource

if TYPE_CHECKING:
    from sortique.data.config_manager import ConfigManager
    from sortique.engine.metadata.exif_extractor import ExifResult


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class DateResult:
    """Outcome of date extraction for a single file."""

    date: datetime | None = None
    source: DateSource = DateSource.NONE
    timezone_offset: str | None = None
    confidence: float = 0.0


# ---------------------------------------------------------------------------
# Default filename date patterns (mirrors defaults.json)
# ---------------------------------------------------------------------------

DEFAULT_PATTERNS: list[str] = [
    # YYYY-MM-DD HH-MM-SS  (dash / underscore / dot separated)
    r"(?P<Y>\d{4})[\-_.](?P<m>\d{2})[\-_.](?P<d>\d{2})[\-_. T](?P<H>\d{2})[\-_.](?P<M>\d{2})[\-_.](?P<S>\d{2})",
    # YYYYMMDD_HHMMSS  (compact, optional separator before time)
    r"(?P<Y>\d{4})(?P<m>\d{2})(?P<d>\d{2})[\-_. T]?(?P<H>\d{2})(?P<M>\d{2})(?P<S>\d{2})",
    # YYYY-MM-DD  (date only)
    r"(?P<Y>\d{4})[\-_.](?P<m>\d{2})[\-_.](?P<d>\d{2})",
    # DD-MM-YYYY  (European day-first)
    r"(?P<d>\d{2})[\-_.](?P<m>\d{2})[\-_.](?P<Y>\d{4})",
]


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class DateParser:
    """Multi-source date extraction with fallback chain.

    Extraction priority:

    1. EXIF metadata         — confidence **1.0**, source ``METADATA``
    2. Filename regex        — confidence **0.8**, source ``PARSED``
    3. Parent-folder regex   — confidence **0.6**, source ``PARSED``
    4. Nearby-file inference  — confidence **0.3**, source ``INFERRED``

    Public methods never raise.
    """

    def __init__(self, config: ConfigManager) -> None:
        compiled = config.date_regex_patterns
        if compiled:
            self._patterns: list[re.Pattern[str]] = compiled
        else:
            self._patterns = [re.compile(p) for p in DEFAULT_PATTERNS]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_date(
        self,
        filepath: str,
        exif_result: ExifResult | None = None,
        sibling_files: list[datetime] | None = None,
    ) -> DateResult:
        """Extract the best date for *filepath* using the fallback chain.

        Parameters
        ----------
        filepath:
            Absolute or relative path to the file.
        exif_result:
            Pre-extracted EXIF data (from :class:`ExifExtractor`).
        sibling_files:
            Already-extracted dates from files in the same directory,
            used for nearby-file inference when higher-priority sources
            are unavailable.
        """
        # 1. EXIF metadata
        if exif_result is not None:
            result = self._from_exif(exif_result)
            if result.date is not None:
                return result

        # 2. Filename
        result = self._from_filename(filepath)
        if result.date is not None:
            return result

        # 3. Parent folder name
        result = self._from_folder_name(filepath)
        if result.date is not None:
            return result

        # 4. Nearby file inference
        if sibling_files:
            result = self._from_nearby_files(sibling_files)
            if result.date is not None:
                return result

        return DateResult()

    # ------------------------------------------------------------------
    # Tier 1 — EXIF metadata
    # ------------------------------------------------------------------

    def _from_exif(self, exif_result: ExifResult) -> DateResult:
        """Extract date from EXIF with priority: Original > Digitized > Modified."""
        for dt in (
            exif_result.date_original,
            exif_result.date_digitized,
            exif_result.date_modified,
        ):
            if dt is not None:
                return DateResult(
                    date=dt,
                    source=DateSource.METADATA,
                    timezone_offset=exif_result.timezone_offset,
                    confidence=1.0,
                )
        return DateResult()

    # ------------------------------------------------------------------
    # Tier 2 — Filename patterns
    # ------------------------------------------------------------------

    def _from_filename(self, filepath: str) -> DateResult:
        """Extract date from the filename using configured regex patterns."""
        filename = Path(filepath).name
        return self._match_patterns(filename, confidence=0.8)

    # ------------------------------------------------------------------
    # Tier 3 — Folder name
    # ------------------------------------------------------------------

    def _from_folder_name(self, filepath: str) -> DateResult:
        """Extract date from the immediate parent folder name."""
        folder_name = Path(filepath).parent.name
        if not folder_name:
            return DateResult()
        return self._match_patterns(folder_name, confidence=0.6)

    # ------------------------------------------------------------------
    # Tier 4 — Nearby file inference
    # ------------------------------------------------------------------

    def _from_nearby_files(self, sibling_files: list[datetime]) -> DateResult:
        """Infer date when >50 % of sibling files share the same calendar day."""
        if not sibling_files:
            return DateResult()

        day_counts: Counter[datetime] = Counter()
        for dt in sibling_files:
            day = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            day_counts[day] += 1

        if not day_counts:
            return DateResult()

        most_common_day, count = day_counts.most_common(1)[0]
        total = len(sibling_files)

        # Strict majority: more than half, not exactly half.
        if count / total > 0.5:
            return DateResult(
                date=most_common_day,
                source=DateSource.INFERRED,
                confidence=0.3,
            )

        return DateResult()

    # ------------------------------------------------------------------
    # Pattern matching helper
    # ------------------------------------------------------------------

    def _match_patterns(self, text: str, confidence: float) -> DateResult:
        """Try each compiled pattern against *text*; return first valid match."""
        for pattern in self._patterns:
            m = pattern.search(text)
            if m is None:
                continue

            groups = m.groupdict()
            year = int(groups.get("Y", "0"))
            month = int(groups.get("m", "0"))
            day = int(groups.get("d", "0"))
            hour = int(groups.get("H", "0"))
            minute = int(groups.get("M", "0"))
            second = int(groups.get("S", "0"))

            if not self._validate_date(year, month, day, hour, minute, second):
                continue

            try:
                dt = datetime(year, month, day, hour, minute, second)
            except ValueError:
                # Calendar-impossible date (e.g. Feb 30).
                continue

            # Reduce confidence for ambiguous DD-MM / MM-DD dates.
            actual_confidence = confidence
            if self._is_ambiguous(pattern, day, month):
                actual_confidence = confidence * 0.5

            return DateResult(
                date=dt,
                source=DateSource.PARSED,
                confidence=actual_confidence,
            )

        return DateResult()

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_date(
        year: int,
        month: int,
        day: int,
        hour: int = 0,
        minute: int = 0,
        second: int = 0,
    ) -> bool:
        """Return ``True`` when all components are within sane ranges.

        Does **not** verify calendar correctness (e.g. Feb 30); the
        ``datetime`` constructor handles that separately.
        """
        if not (1900 <= year <= 2100):
            return False
        if not (1 <= month <= 12):
            return False
        if not (1 <= day <= 31):
            return False
        if not (0 <= hour <= 23):
            return False
        if not (0 <= minute <= 59):
            return False
        if not (0 <= second <= 59):
            return False
        return True

    @staticmethod
    def _is_ambiguous(pattern: re.Pattern[str], day: int, month: int) -> bool:
        """Return ``True`` when a DD-MM match could equally be MM-DD.

        A date is ambiguous when the regex puts the *day* named group
        before the *month* group (European DD-MM-YYYY layout) **and**
        both extracted values are ≤ 12 so either could be month or day.
        """
        if day > 12 or month > 12:
            return False

        source = pattern.pattern
        d_pos = source.find("(?P<d>")
        m_pos = source.find("(?P<m>")
        y_pos = source.find("(?P<Y>")

        if d_pos < 0 or m_pos < 0 or y_pos < 0:
            return False

        # DD-MM-YYYY layout: day group appears first.
        return d_pos < m_pos and d_pos < y_pos
