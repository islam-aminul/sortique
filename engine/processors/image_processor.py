"""Image processing: copy originals and generate JPEG/PNG exports."""

from __future__ import annotations

import io
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import piexif
from PIL import Image

from sortique.constants import DEFAULT_JPEG_QUALITY, IMAGE_EXTENSIONS, MAX_RESOLUTION
from sortique.data.file_system import FileSystemHelper

if TYPE_CHECKING:
    from sortique.data.config_manager import ConfigManager
    from sortique.engine.metadata.exif_extractor import ExifResult


# ---------------------------------------------------------------------------
# Optional format support
# ---------------------------------------------------------------------------

_RAWPY_AVAILABLE = False
try:
    import rawpy  # type: ignore[import-untyped]

    _RAWPY_AVAILABLE = True
except ImportError:
    pass

_HEIF_AVAILABLE = False
try:
    import pillow_heif  # type: ignore[import-untyped]

    pillow_heif.register_heif_opener()
    _HEIF_AVAILABLE = True
except ImportError:
    pass


# ---------------------------------------------------------------------------
# RAW extensions recognised by rawpy
# ---------------------------------------------------------------------------

_RAW_EXTENSIONS: frozenset[str] = frozenset({
    ".raw", ".cr2", ".cr3", ".nef", ".nrw", ".arw", ".srf", ".sr2",
    ".dng", ".orf", ".erf", ".raf", ".rw2", ".rwl", ".pef", ".ptx",
    ".srw", ".x3f", ".3fr", ".mef", ".mos", ".mrw", ".kdc", ".dcr",
    ".iiq", ".gpr",
})

_HEIF_EXTENSIONS: frozenset[str] = frozenset({".heic", ".heif"})


# ---------------------------------------------------------------------------
# Result / errors
# ---------------------------------------------------------------------------

@dataclass
class ExportResult:
    """Outcome of :meth:`ImageProcessor.generate_export`."""

    success: bool
    export_path: str
    format: str                           # "jpeg" or "png"
    original_size: tuple[int, int]
    export_size: tuple[int, int]
    was_downscaled: bool
    was_rotated: bool
    transparency_preserved: bool
    warnings: list[str] = field(default_factory=list)


class UnsupportedFormatError(Exception):
    """Raised when the image cannot be opened by any available library."""


# ---------------------------------------------------------------------------
# EXIF orientation transforms
# ---------------------------------------------------------------------------

# Mapping from EXIF Orientation tag to Pillow transpose operation(s).
# Each value is a tuple of Pillow transpose constants to apply in order.
_ORIENTATION_OPS: dict[int, tuple[int, ...]] = {
    1: (),                                           # normal
    2: (Image.FLIP_LEFT_RIGHT,),                     # mirrored
    3: (Image.ROTATE_180,),                          # upside-down
    4: (Image.FLIP_TOP_BOTTOM,),                     # mirrored + 180
    5: (Image.FLIP_LEFT_RIGHT, Image.ROTATE_90),     # mirrored + 90 CW
    6: (Image.ROTATE_270,),                          # 90 CW
    7: (Image.FLIP_LEFT_RIGHT, Image.ROTATE_270),    # mirrored + 270 CW
    8: (Image.ROTATE_90,),                           # 270 CW
}


# ---------------------------------------------------------------------------
# ImageProcessor
# ---------------------------------------------------------------------------

class ImageProcessor:
    """Processes images: copies originals and generates JPEG exports."""

    def __init__(self, config: ConfigManager) -> None:
        self.config = config

    # ------------------------------------------------------------------
    # Public: copy original
    # ------------------------------------------------------------------

    def copy_original(
        self,
        source: str,
        destination: str,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> bool:
        """Copy *source* to *destination* via atomic copy.  Never modifies the original."""
        return FileSystemHelper.atomic_copy(source, destination, progress_callback)

    # ------------------------------------------------------------------
    # Public: generate export
    # ------------------------------------------------------------------

    def generate_export(
        self,
        source: str,
        export_path: str,
        exif_result: ExifResult | None = None,
    ) -> ExportResult:
        """Generate a JPEG (or PNG) export copy of *source*.

        See the class-level docstring for the full rule set.
        """
        warnings: list[str] = []

        # --- open ---
        img = self._open_image(source)
        original_size = img.size

        was_rotated = False
        transparency_preserved = False

        # --- transparency check ---
        if img.mode == "RGBA":
            if self._has_real_transparency(img):
                # Save as PNG, preserving transparency.
                export_path = _swap_extension(export_path, ".png")
                img.save(export_path, format="PNG")
                return ExportResult(
                    success=True,
                    export_path=export_path,
                    format="png",
                    original_size=original_size,
                    export_size=img.size,
                    was_downscaled=False,
                    was_rotated=False,
                    transparency_preserved=True,
                    warnings=warnings,
                )
            else:
                # Fully opaque RGBA → convert to RGB.
                img = img.convert("RGB")
        elif img.mode == "P":
            # Palette images with transparency
            if "transparency" in img.info:
                img = img.convert("RGBA")
                if self._has_real_transparency(img):
                    export_path = _swap_extension(export_path, ".png")
                    img.save(export_path, format="PNG")
                    return ExportResult(
                        success=True,
                        export_path=export_path,
                        format="png",
                        original_size=original_size,
                        export_size=img.size,
                        was_downscaled=False,
                        was_rotated=False,
                        transparency_preserved=True,
                        warnings=warnings,
                    )
                else:
                    img = img.convert("RGB")
            else:
                img = img.convert("RGB")
        elif img.mode == "LA":
            img = img.convert("RGBA")
            if self._has_real_transparency(img):
                export_path = _swap_extension(export_path, ".png")
                img.save(export_path, format="PNG")
                return ExportResult(
                    success=True,
                    export_path=export_path,
                    format="png",
                    original_size=original_size,
                    export_size=img.size,
                    was_downscaled=False,
                    was_rotated=False,
                    transparency_preserved=True,
                    warnings=warnings,
                )
            else:
                img = img.convert("RGB")
        elif img.mode != "RGB":
            img = img.convert("RGB")

        # --- auto-rotate ---
        orientation = exif_result.orientation if exif_result else None
        if orientation is not None and orientation in _ORIENTATION_OPS:
            ops = _ORIENTATION_OPS[orientation]
            if ops:
                for op in ops:
                    img = img.transpose(op)
                was_rotated = True

        # --- downscale ---
        max_w, max_h = self.config.max_resolution
        was_downscaled = False
        img, was_downscaled = self._fit_within(img, max_w, max_h)

        export_size = img.size

        # --- EXIF handling ---
        exif_bytes: bytes | None = None
        try:
            raw_exif = _extract_exif_bytes(source)
            if raw_exif:
                exif_bytes = self._strip_thumbnail(raw_exif)
        except Exception as exc:
            warnings.append(f"EXIF preservation failed: {exc}")

        # --- save ---
        export_path = _swap_extension(export_path, ".jpg")
        quality = self.config.jpeg_quality

        save_kwargs: dict = {
            "format": "JPEG",
            "quality": quality,
        }
        if exif_bytes:
            save_kwargs["exif"] = exif_bytes

        os.makedirs(os.path.dirname(export_path), exist_ok=True)
        img.save(export_path, **save_kwargs)

        return ExportResult(
            success=True,
            export_path=export_path,
            format="jpeg",
            original_size=original_size,
            export_size=export_size,
            was_downscaled=was_downscaled,
            was_rotated=was_rotated,
            transparency_preserved=False,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Internal: open image
    # ------------------------------------------------------------------

    def _open_image(self, filepath: str) -> Image.Image:
        """Open an image with the appropriate backend.

        * Standard formats → ``PIL.Image.open()``
        * RAW (CR2, NEF, …) → ``rawpy`` post-process → PIL Image
        * HEIC/HEIF → ``pillow_heif`` (registered as Pillow plugin)

        Raises :class:`UnsupportedFormatError` on failure.
        """
        ext = os.path.splitext(filepath)[1].lower()

        # --- RAW formats ---
        if ext in _RAW_EXTENSIONS:
            if not _RAWPY_AVAILABLE:
                raise UnsupportedFormatError(
                    f"rawpy is not installed; cannot open RAW file: {filepath}"
                )
            try:
                with rawpy.imread(filepath) as raw:
                    rgb = raw.postprocess()
                return Image.fromarray(rgb)
            except Exception as exc:
                raise UnsupportedFormatError(
                    f"rawpy failed to open {filepath}: {exc}"
                ) from exc

        # --- HEIC/HEIF ---
        if ext in _HEIF_EXTENSIONS:
            if not _HEIF_AVAILABLE:
                raise UnsupportedFormatError(
                    f"pillow-heif is not installed; cannot open HEIF file: {filepath}"
                )
            # pillow_heif registers itself so Pillow can open HEIF natively.
            try:
                return Image.open(filepath)
            except Exception as exc:
                raise UnsupportedFormatError(
                    f"Failed to open HEIF file {filepath}: {exc}"
                ) from exc

        # --- Standard Pillow ---
        try:
            img = Image.open(filepath)
            img.load()  # force decode
            return img
        except Exception as exc:
            raise UnsupportedFormatError(
                f"Pillow cannot open {filepath}: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Internal: transparency
    # ------------------------------------------------------------------

    def _has_real_transparency(self, image: Image.Image) -> bool:
        """Return ``True`` when *image* has any pixel with alpha < 255.

        Optimised: checks the minimum value of the alpha channel rather
        than iterating over every pixel.
        """
        if image.mode != "RGBA":
            return False

        alpha = image.getchannel("A")
        return alpha.getextrema()[0] < 255

    # ------------------------------------------------------------------
    # Internal: auto-rotate
    # ------------------------------------------------------------------

    def _auto_rotate(self, image: Image.Image, orientation: int) -> Image.Image:
        """Physically rotate/mirror *image* based on EXIF *orientation*."""
        ops = _ORIENTATION_OPS.get(orientation, ())
        for op in ops:
            image = image.transpose(op)
        return image

    # ------------------------------------------------------------------
    # Internal: downscale
    # ------------------------------------------------------------------

    def _fit_within(
        self,
        image: Image.Image,
        max_width: int,
        max_height: int,
    ) -> tuple[Image.Image, bool]:
        """Downscale *image* to fit within *max_width* × *max_height*.

        Never upscales.  Preserves aspect ratio.  Uses LANCZOS resampling.
        Returns ``(image, was_downscaled)``.
        """
        w, h = image.size

        if w <= max_width and h <= max_height:
            return image, False

        ratio = min(max_width / w, max_height / h)
        new_w = max(1, int(w * ratio))
        new_h = max(1, int(h * ratio))

        return image.resize((new_w, new_h), Image.LANCZOS), True

    # ------------------------------------------------------------------
    # Internal: EXIF thumbnail strip
    # ------------------------------------------------------------------

    def _strip_thumbnail(self, exif_bytes: bytes) -> bytes:
        """Remove IFD1 (thumbnail) from EXIF while preserving everything else."""
        try:
            exif_dict = piexif.load(exif_bytes)
        except Exception:
            return exif_bytes

        # Clear thumbnail IFD and thumbnail data.
        exif_dict["1st"] = {}
        exif_dict["thumbnail"] = None

        try:
            return piexif.dump(exif_dict)
        except Exception:
            return exif_bytes


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _swap_extension(path: str, new_ext: str) -> str:
    """Replace the extension of *path* with *new_ext*."""
    base, _ = os.path.splitext(path)
    return base + new_ext


def _extract_exif_bytes(filepath: str) -> bytes | None:
    """Read raw EXIF bytes from *filepath* using Pillow, or ``None``."""
    try:
        with Image.open(filepath) as img:
            info = img.info
            exif_data = info.get("exif")
            if isinstance(exif_data, bytes) and exif_data:
                return exif_data
    except Exception:
        pass
    return None
