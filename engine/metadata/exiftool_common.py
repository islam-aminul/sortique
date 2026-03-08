"""Shared ExifTool subprocess utilities (detection, invocation, parsing).

Provides cached availability detection, a single ``run_exiftool()`` helper
that invokes ``exiftool -json -n <filepath>``, and a date-string parser.
Used by :mod:`exif_extractor` and :mod:`video_metadata`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime
from functools import lru_cache


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def is_exiftool_available() -> bool:
    """Return ``True`` when ``exiftool`` is found on the system PATH.

    The result is cached for the lifetime of the process so the PATH
    lookup only happens once.
    """
    return shutil.which("exiftool") is not None


# ---------------------------------------------------------------------------
# Subprocess invocation
# ---------------------------------------------------------------------------

def run_exiftool(filepath: str, *, timeout: int = 30) -> dict | None:
    """Run ``exiftool -json -n <filepath>`` and return the first JSON object.

    Returns ``None`` if ExifTool is not available, the subprocess fails,
    or the output cannot be parsed.  Never raises.
    """
    if not is_exiftool_available():
        return None

    try:
        proc = subprocess.run(
            ["exiftool", "-json", "-n", filepath],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            return None

        data = json.loads(proc.stdout)
        if not data:
            return None

        return data[0]
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

def parse_exiftool_date(date_str: str | None) -> datetime | None:
    """Parse an ExifTool / EXIF date string to :class:`datetime`.

    Handles the standard ``YYYY:MM:DD HH:MM:SS`` format, ISO variants,
    timezone suffixes (stripped for naive datetime), and the all-zeros
    sentinel ``0000:00:00 00:00:00``.
    """
    if date_str is None:
        return None

    if isinstance(date_str, bytes):
        date_str = date_str.decode("utf-8", errors="replace")

    date_str = date_str.strip()
    if not date_str or date_str.startswith("0000"):
        return None

    # Strip timezone suffix before parsing (e.g. "+05:30", "Z", "-08:00").
    core = date_str
    if len(core) > 10:
        # Remove trailing 'Z'
        if core.endswith("Z"):
            core = core[:-1]
        # Remove +HH:MM / -HH:MM offset at end
        for sep_pos in (len(core) - 6, len(core) - 5):
            if 0 < sep_pos < len(core) and core[sep_pos] in ("+", "-"):
                core = core[:sep_pos]
                break
    core = core.strip()

    formats = (
        "%Y:%m:%d %H:%M:%S",     # standard EXIF
        "%Y:%m:%d %H:%M",         # missing seconds
        "%Y-%m-%d %H:%M:%S",      # dash-separated
        "%Y-%m-%dT%H:%M:%S",      # ISO with T
        "%Y-%m-%dT%H:%M:%S.%f",   # ISO with fractional seconds
        "%Y:%m:%d",               # date only
    )
    for fmt in formats:
        try:
            return datetime.strptime(core, fmt)
        except ValueError:
            continue
    return None
