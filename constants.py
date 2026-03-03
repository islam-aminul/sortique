"""Sortique constants, enumerations, and extension mappings."""

from __future__ import annotations

from enum import Enum


# ---------------------------------------------------------------------------
# Enumerations (str + Enum for JSON-friendly serialisation)
# ---------------------------------------------------------------------------

class FileType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"
    SIDECAR = "sidecar"
    UNKNOWN = "unknown"


class SessionState(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    STOPPED = "stopped"
    ERROR = "error"
    UNDONE = "undone"


class FileStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    ERROR = "error"


class DateSource(str, Enum):
    METADATA = "metadata"
    PARSED = "parsed"
    INFERRED = "inferred"
    NONE = "none"


class ExifStatus(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    ERROR = "error"
    NONE = "none"


class DupMatchType(str, Enum):
    EXACT = "exact"
    PERCEPTUAL = "perceptual"


class PairPolicy(str, Enum):
    KEEP_BOTH = "keep_both"
    KEEP_RAW = "keep_raw"
    KEEP_JPEG = "keep_jpeg"


# ---------------------------------------------------------------------------
# Numeric / size constants
# ---------------------------------------------------------------------------

LARGE_FILE_THRESHOLD: int = 100 * 1024 * 1024          # 100 MB
LOW_SPACE_THRESHOLD: int = 5 * 1024 * 1024 * 1024      # 5 GB

PROGRESS_INTERVAL: float = 0.2                          # seconds
FLUSH_INTERVAL: int = 100                               # records before flush

DEFAULT_JPEG_QUALITY: int = 85
MAX_RESOLUTION: tuple[int, int] = (3840, 2160)

DEFAULT_THREADS: int = 4
MAX_THREADS: int = 16

MAX_CONFLICT_ATTEMPTS: int = 10_000

SPACE_OVERHEAD_FACTOR: float = 1.3
SPACE_BUFFER_FACTOR: float = 1.1


# ---------------------------------------------------------------------------
# Extension mappings  (lower-case, leading dot)
# ---------------------------------------------------------------------------

IMAGE_EXTENSIONS: frozenset[str] = frozenset({
    # Common raster
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif", ".webp",
    ".heic", ".heif", ".avif", ".jxl",
    # RAW formats
    ".raw", ".cr2", ".cr3", ".nef", ".nrw", ".arw", ".srf", ".sr2",
    ".dng", ".orf", ".erf", ".raf", ".rw2", ".rwl", ".pef", ".ptx",
    ".srw", ".x3f", ".3fr", ".mef", ".mos", ".mrw", ".kdc", ".dcr",
    ".iiq", ".gpr",
    # Other
    ".ico", ".svg", ".psd", ".ai", ".eps", ".indd",
})

VIDEO_EXTENSIONS: frozenset[str] = frozenset({
    ".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm", ".m4v",
    ".mpg", ".mpeg", ".3gp", ".3g2", ".mts", ".m2ts", ".ts", ".vob",
    ".ogv", ".divx", ".asf", ".rm", ".rmvb", ".f4v",
})

AUDIO_EXTENSIONS: frozenset[str] = frozenset({
    ".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a", ".opus",
    ".aiff", ".aif", ".alac", ".ape", ".mid", ".midi", ".amr", ".ac3",
    ".dts", ".pcm", ".caf", ".mka",
})

DOCUMENT_EXTENSIONS: frozenset[str] = frozenset({
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".odt", ".ods", ".odp", ".txt", ".rtf", ".csv", ".tsv",
    ".md", ".html", ".htm", ".xml", ".json", ".yaml", ".yml",
    ".tex", ".log", ".epub", ".mobi",
})

SIDECAR_EXTENSIONS: frozenset[str] = frozenset({
    ".thm", ".srt", ".sub", ".lrc", ".xmp", ".aae",
})

EXTENSION_MAP: dict[str, FileType] = {}
for _ext in IMAGE_EXTENSIONS:
    EXTENSION_MAP[_ext] = FileType.IMAGE
for _ext in VIDEO_EXTENSIONS:
    EXTENSION_MAP[_ext] = FileType.VIDEO
for _ext in AUDIO_EXTENSIONS:
    EXTENSION_MAP[_ext] = FileType.AUDIO
for _ext in DOCUMENT_EXTENSIONS:
    EXTENSION_MAP[_ext] = FileType.DOCUMENT
for _ext in SIDECAR_EXTENSIONS:
    EXTENSION_MAP[_ext] = FileType.SIDECAR


# ---------------------------------------------------------------------------
# System / hidden file filters
# ---------------------------------------------------------------------------

HIDDEN_SYSTEM_FILES: frozenset[str] = frozenset({
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
})

SKIP_DIRS: frozenset[str] = frozenset({
    ".git",
    "node_modules",
    "__pycache__",
    ".svn",
    ".hg",
})


# ---------------------------------------------------------------------------
# Cloud stub patterns
# ---------------------------------------------------------------------------

CLOUD_STUB_PATTERNS: dict[str, list[str]] = {
    "icloud":   [".icloud"],
    "onedrive": [".cloud", ".odopen"],
    "dropbox":  [".dropbox", ".dropbox.attr"],
    "gdrive":   [".gdoc", ".gsheet", ".gslides"],
}
