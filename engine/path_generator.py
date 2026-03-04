"""Destination path generation with adaptive naming and conflict resolution."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from sortique.constants import EXTENSION_MAP, MAX_CONFLICT_ATTEMPTS, FileType
from sortique.data.file_system import FileSystemHelper

if TYPE_CHECKING:
    from sortique.data.config_manager import ConfigManager
    from sortique.engine.metadata.date_parser import DateResult
    from sortique.engine.metadata.exif_extractor import ExifResult


# ---------------------------------------------------------------------------
# Category groupings used by _build_category_path
# ---------------------------------------------------------------------------

_MAKE_MODEL_YEAR_CATEGORIES = frozenset({"Originals", "RAW"})

_YEAR_ONLY_CATEGORIES = frozenset({
    "Edited", "Export", "Motion Photos",
    "Voice Notes", "WhatsApp",
})

_STATIC_CATEGORIES = frozenset({
    "Screenshots", "Social Media", "Hidden", "Movies", "Songs",
})


# ---------------------------------------------------------------------------
# PathGenerator
# ---------------------------------------------------------------------------

class PathGenerator:
    """Generates destination paths with adaptive naming and conflict resolution.

    Path structure::

        {destination_root}/{category_path}/{filename}
    """

    def __init__(self, config: ConfigManager, destination_root: str) -> None:
        self.config = config
        self.destination_root = destination_root

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(
        self,
        category: str,
        original_filename: str,
        original_ext: str,
        date_result: DateResult | None,
        exif: ExifResult | None,
        is_burst: bool = False,
        burst_index: int = 0,
        is_export: bool = False,
    ) -> str:
        """Generate the full destination path.

        Parameters
        ----------
        category:
            Category string produced by :class:`Categorizer`.
        original_filename:
            Stem of the original file (no extension).
        original_ext:
            Extension including the leading dot (e.g. ``".jpg"``).
            A bare extension without the dot is also accepted.
        date_result:
            Extracted date information (may be ``None``).
        exif:
            Extracted EXIF data (may be ``None``).
        is_burst:
            ``True`` when the file belongs to a burst sequence.
        burst_index:
            Zero-based position in the burst (only used when *is_burst*).
        is_export:
            ``True`` to use the flat export path: ``Exports/{Year}/``.
        """
        year = date_result.date.year if date_result and date_result.date else None
        make = exif.make if exif else None
        model = exif.model if exif else None

        # --- category path ---
        if is_export:
            parts: list[str] = ["Exports"]
            if year is not None:
                parts.append(str(year))
            cat_path = os.path.join(*parts)
        else:
            effective_category = category
            if category == "Collection":
                ext_lower = (original_ext or "").lower()
                if ext_lower and not ext_lower.startswith("."):
                    ext_lower = "." + ext_lower
                file_type = EXTENSION_MAP.get(ext_lower, FileType.UNKNOWN)
                effective_category = f"Collection/{file_type.value.title()}"

            cat_path = self._build_category_path(
                effective_category, year, make, model,
            )

        # --- filename ---
        filename = self.generate_filename(
            original_filename, original_ext,
            date_result, exif, is_burst, burst_index,
        )

        return os.path.join(self.destination_root, cat_path, filename)

    def generate_filename(
        self,
        original_name: str,
        original_ext: str,
        date_result: DateResult | None,
        exif: ExifResult | None,
        is_burst: bool = False,
        burst_index: int = 0,
    ) -> str:
        """Generate the filename using an adaptive template.

        Non-empty segments are joined with ``" -- "``.

        * Full:      ``YYYY-MM-DD HH-MM-SS -- {Make} - {Model} -- {Name}.ext``
        * No camera: ``YYYY-MM-DD HH-MM-SS -- {Name}.ext``
        * No date:   ``{Name}.ext``
        * Burst:     ``YYYY-MM-DD HH-MM-SS-NNN -- {Make} - {Model} -- {Name}.ext``

        The result is passed through :meth:`FileSystemHelper.sanitize_filename`.
        """
        # --- date segment ---
        date_str: str | None = None
        if date_result and date_result.date:
            dt = date_result.date
            date_str = (
                f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d} "
                f"{dt.hour:02d}-{dt.minute:02d}-{dt.second:02d}"
            )
            if is_burst:
                date_str = f"{date_str}-{burst_index:03d}"

        # --- make / model segment ---
        make_model = self._format_make_model(
            exif.make if exif else None,
            exif.model if exif else None,
        )

        # --- assemble segments ---
        segments: list[str] = []
        if date_str:
            segments.append(date_str)
        if make_model:
            segments.append(make_model)
        segments.append(original_name)

        stem = " -- ".join(segments)

        # --- extension ---
        ext = original_ext or ""
        if ext and not ext.startswith("."):
            ext = "." + ext

        raw_filename = f"{stem}{ext}" if ext else stem
        return FileSystemHelper.sanitize_filename(raw_filename)

    def resolve_conflict(self, dest_path: str) -> str:
        """Append ``_1``, ``_2``, … before the extension until the path is free.

        Raises :class:`FileExistsError` after ``MAX_CONFLICT_ATTEMPTS``.
        """
        if not os.path.exists(dest_path):
            return dest_path

        base, ext = os.path.splitext(dest_path)

        for i in range(1, MAX_CONFLICT_ATTEMPTS + 1):
            candidate = f"{base}_{i}{ext}"
            if not os.path.exists(candidate):
                return candidate

        raise FileExistsError(
            f"All {MAX_CONFLICT_ATTEMPTS} conflict resolution attempts "
            f"exhausted for: {dest_path}"
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_category_path(
        self,
        category: str,
        year: int | None,
        make: str | None,
        model: str | None,
    ) -> str:
        """Build the folder sub-path for a given category."""

        # --- make/model + year  (Originals, RAW) ---
        if category in _MAKE_MODEL_YEAR_CATEGORIES:
            make_model = self._format_make_model(make, model)
            parts: list[str] = [category]
            if make_model:
                parts.append(make_model)
            if year is not None:
                parts.append(str(year))
            return os.path.join(*parts)

        # --- year only  (Edited, Export, Motion Photos, …) ---
        if category in _YEAR_ONLY_CATEGORIES:
            if year is not None:
                return os.path.join(category, str(year))
            return category

        # --- Originals/Unknown  (year sub-folder) ---
        if category == "Originals/Unknown":
            parts = ["Originals", "Unknown"]
            if year is not None:
                parts.append(str(year))
            return os.path.join(*parts)

        # --- static  (Screenshots, Social Media, Hidden, Movies, Songs) ---
        if category in _STATIC_CATEGORIES:
            return category

        # --- Documents/*, Collection/* — preserve structural slashes ---
        components = category.split("/")
        return os.path.join(*components)

    def _format_make_model(
        self, make: str | None, model: str | None,
    ) -> str | None:
        """Format camera make / model for use in folder and file names.

        Returns ``None`` when both inputs are ``None``.
        If *model* already starts with *make* (case-insensitive) the
        redundant prefix is elided so ``"Canon" + "Canon EOS R5"``
        yields ``"Canon EOS R5"`` rather than ``"Canon - Canon EOS R5"``.
        """
        if make is None and model is None:
            return None

        if make is not None and model is not None:
            mk = make.strip()
            md = model.strip()
            if md.lower().startswith(mk.lower()):
                formatted = md
            else:
                formatted = f"{mk} - {md}"
        elif make is not None:
            formatted = make.strip()
        else:
            formatted = model.strip()  # type: ignore[union-attr]

        return FileSystemHelper.sanitize_filename(formatted)
