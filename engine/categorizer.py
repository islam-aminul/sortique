"""File categorisation engine — assigns organisation categories based on metadata."""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sortique.data.config_manager import ConfigManager
    from sortique.engine.metadata.audio_metadata import AudioMetadata
    from sortique.engine.metadata.exif_extractor import ExifResult
    from sortique.engine.metadata.video_metadata import VideoMetadata


# ---------------------------------------------------------------------------
# RAW format identifiers (lower-case, no leading dot)
# ---------------------------------------------------------------------------

_RAW_FORMATS: frozenset[str] = frozenset({
    "raw", "cr2", "cr3", "nef", "nrw", "arw", "srf", "sr2",
    "dng", "orf", "erf", "raf", "rw2", "rwl", "pef", "ptx",
    "srw", "x3f", "3fr", "mef", "mos", "mrw", "kdc", "dcr",
    "iiq", "gpr",
})


# ---------------------------------------------------------------------------
# Common display aspect ratios as integer pairs  (checked both orientations)
# ---------------------------------------------------------------------------

_DISPLAY_RATIOS: list[tuple[int, int]] = [
    (16, 9),    # standard widescreen
    (4, 3),     # traditional display
    (18, 39),   # 9 : 19.5  modern phone (×2 to keep integers)
]


# ---------------------------------------------------------------------------
# Document extension → sub-category mapping
# ---------------------------------------------------------------------------

_DOC_PDF: frozenset[str] = frozenset({".pdf"})
_DOC_TEXT: frozenset[str] = frozenset({".txt", ".md", ".rtf"})
_DOC_WORD: frozenset[str] = frozenset({".doc", ".docx"})
_DOC_EXCEL: frozenset[str] = frozenset({".xls", ".xlsx"})
_DOC_PPT: frozenset[str] = frozenset({".ppt", ".pptx"})
_DOC_CODE: frozenset[str] = frozenset({
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".cpp",
    ".h", ".hpp", ".cs", ".go", ".rs", ".rb", ".php", ".kt",
    ".swift", ".scala", ".r", ".m", ".sh", ".bash", ".ps1",
    ".html", ".htm", ".css", ".xml", ".json", ".yaml", ".yml",
    ".sql", ".lua", ".pl", ".pm",
})


# ---------------------------------------------------------------------------
# Categoriser
# ---------------------------------------------------------------------------

class Categorizer:
    """Assigns files to organisation categories based on metadata and rules.

    All pattern matching uses configurable rules from :class:`ConfigManager`.
    """

    def __init__(self, config: ConfigManager) -> None:
        self.config = config

    # ------------------------------------------------------------------
    # Image
    # ------------------------------------------------------------------

    def categorize_image(
        self,
        filepath: str,
        exif: ExifResult,
        file_type_detail: str,
    ) -> str:
        """Categorise an image file.  Returns a category string.

        Priority (first match wins):

        1. ``RAW``
        2. ``Edited``
        3. ``Screenshots``
        4. ``Social Media``
        5. ``Hidden``
        6. ``Originals``
        7. ``Export``
        8. ``Collection``
        """
        filename = Path(filepath).name
        ext = Path(filepath).suffix.lower()

        # 1. RAW --------------------------------------------------------
        if file_type_detail.lower() in _RAW_FORMATS:
            return "RAW"

        # 2. Edited -----------------------------------------------------
        if exif.software:
            editor_patterns = self.config.editor_patterns
            exclusion_patterns = self.config.editor_exclusions
            is_editor = any(p.search(exif.software) for p in editor_patterns)
            is_excluded = (
                any(p.search(exif.software) for p in exclusion_patterns)
                if exclusion_patterns
                else False
            )
            if is_editor and not is_excluded:
                return "Edited"

        # 3. Screenshots ------------------------------------------------
        screenshot_patterns = self.config.get("screenshot_filename_patterns", [])
        if self._matches_glob_patterns(filename, screenshot_patterns):
            return "Screenshots"

        has_camera = exif.make is not None
        has_gps = exif.gps_lat is not None or exif.gps_lon is not None

        if (
            self._matches_screenshot_resolution(exif.width, exif.height)
            and not has_camera
            and not has_gps
        ):
            return "Screenshots"

        if self._matches_display_heuristic(
            exif.width, exif.height, has_camera, has_gps,
        ):
            return "Screenshots"

        # 4. Social Media -----------------------------------------------
        sm_patterns = self.config.social_media_image_patterns
        if self._matches_glob_patterns(filename, sm_patterns):
            return "Social Media"

        # 5. Hidden (sidecar) -------------------------------------------
        sidecar_exts = self.config.sidecar_extensions
        if ext in {e.lower() for e in sidecar_exts}:
            return "Hidden"

        # 6. Originals --------------------------------------------------
        if exif.make is not None:
            return "Originals"

        # 7. Export -----------------------------------------------------
        has_date = any([
            exif.date_original,
            exif.date_digitized,
            exif.date_modified,
        ])
        if has_date:
            return "Export"

        # 8. Collection -------------------------------------------------
        return "Collection"

    # ------------------------------------------------------------------
    # Video
    # ------------------------------------------------------------------

    def categorize_video(self, filepath: str, video_meta: VideoMetadata) -> str:
        """Categorise a video file.  Returns a category string.

        Priority:

        1. ``Motion Photos``  — duration < 10 s AND filename matches motion patterns
        2. ``Social Media``
        3. ``Originals``      — has make / model
        4. ``Movies``         — duration > 15 minutes (900 s)
        5. ``Originals/Unknown``

        Duration-based rules (1, 4) are skipped when ``duration_unknown``
        is ``True``.
        """
        filename = Path(filepath).name

        # 1. Motion Photos ----------------------------------------------
        has_duration = (
            not video_meta.duration_unknown
            and video_meta.duration_seconds is not None
        )
        if has_duration:
            motion_patterns = self.config.get("motion_photo_patterns", [])
            if (
                video_meta.duration_seconds < 10
                and self._matches_glob_patterns(filename, motion_patterns)
            ):
                return "Motion Photos"

        # 2. Social Media -----------------------------------------------
        sm_patterns = self.config.social_media_video_patterns
        if self._matches_glob_patterns(filename, sm_patterns):
            return "Social Media"

        # 3. Originals --------------------------------------------------
        if video_meta.make is not None or video_meta.model is not None:
            return "Originals"

        # 4. Movies -----------------------------------------------------
        if has_duration and video_meta.duration_seconds > 900:
            return "Movies"

        # 5. Fallback ---------------------------------------------------
        return "Originals/Unknown"

    # ------------------------------------------------------------------
    # Audio
    # ------------------------------------------------------------------

    def categorize_audio(self, filepath: str, audio_meta: AudioMetadata) -> str:
        """Categorise an audio file.  Returns a category string.

        Priority:

        1. ``Voice Notes``  — extension in (.m4a, .aac, .amr) AND filename matches
        2. ``WhatsApp``     — extension in (.opus, .ogg) AND ``PTT-*-WA*``
        3. ``Songs``        — ``has_tags`` is True
        4. ``Collection``
        """
        filename = Path(filepath).name
        ext = Path(filepath).suffix.lower()

        # 1. Voice Notes ------------------------------------------------
        voice_patterns = self.config.get("voice_note_patterns", [])
        if ext in (".m4a", ".aac", ".amr") and self._matches_glob_patterns(
            filename, voice_patterns,
        ):
            return "Voice Notes"

        # 2. WhatsApp ---------------------------------------------------
        if ext in (".opus", ".ogg") and self._matches_glob_patterns(
            filename, ["PTT-*-WA*"],
        ):
            return "WhatsApp"

        # 3. Songs ------------------------------------------------------
        if audio_meta.has_tags:
            return "Songs"

        # 4. Collection -------------------------------------------------
        return "Collection"

    # ------------------------------------------------------------------
    # Document
    # ------------------------------------------------------------------

    def categorize_document(self, filepath: str) -> str:
        """Categorise a document by extension.

        Returns ``Documents/<sub>`` where *<sub>* is one of
        PDF, Text, Word, Excel, PowerPoint, Code, or Other.
        """
        ext = Path(filepath).suffix.lower()

        if ext in _DOC_PDF:
            return "Documents/PDF"
        if ext in _DOC_TEXT:
            return "Documents/Text"
        if ext in _DOC_WORD:
            return "Documents/Word"
        if ext in _DOC_EXCEL:
            return "Documents/Excel"
        if ext in _DOC_PPT:
            return "Documents/PowerPoint"
        if ext in _DOC_CODE:
            return "Documents/Code"
        return "Documents/Other"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _matches_glob_patterns(
        self, filename: str, patterns: list[str],
    ) -> bool:
        """Return ``True`` if *filename* matches any glob-style pattern."""
        return any(fnmatch.fnmatch(filename, p) for p in patterns)

    def _matches_screenshot_resolution(
        self, width: int | None, height: int | None,
    ) -> bool:
        """Check if dimensions match a configured screenshot resolution ± tolerance.

        Both orientations (WxH and HxW) are checked.
        """
        if width is None or height is None:
            return False

        tolerance = self.config.get("screenshot_tolerance", 10)
        resolutions = self.config.screenshot_resolutions

        for res_w, res_h in resolutions:
            # Normal orientation.
            if abs(width - res_w) <= tolerance and abs(height - res_h) <= tolerance:
                return True
            # Rotated orientation.
            if abs(width - res_h) <= tolerance and abs(height - res_w) <= tolerance:
                return True

        return False

    def _matches_display_heuristic(
        self,
        width: int | None,
        height: int | None,
        has_camera: bool,
        has_gps: bool,
    ) -> bool:
        """Heuristic fallback for screenshot detection.

        Returns ``True`` when there is *no camera*, *no GPS*, and the
        dimensions are an exact integer multiple of a common display
        aspect ratio (16∶9, 4∶3, or 9∶19.5).
        """
        if has_camera or has_gps:
            return False
        if width is None or height is None:
            return False
        if width <= 0 or height <= 0:
            return False

        for rw, rh in _DISPLAY_RATIOS:
            # WxH orientation.
            if width % rw == 0 and height % rh == 0 and width // rw == height // rh:
                return True
            # Rotated (HxW) orientation.
            if width % rh == 0 and height % rw == 0 and width // rh == height // rw:
                return True

        return False
