"""Tests for sortique.engine.processors.image_processor."""

from __future__ import annotations

import os
import struct
from io import BytesIO

import piexif
import pytest
from PIL import Image

from sortique.data.config_manager import ConfigManager
from sortique.engine.metadata.exif_extractor import ExifResult
from sortique.engine.processors.image_processor import (
    ExportResult,
    ImageProcessor,
    UnsupportedFormatError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_jpeg(
    tmp_path,
    name: str = "photo.jpg",
    size: tuple[int, int] = (200, 100),
    color: str = "red",
    exif_dict: dict | None = None,
) -> str:
    """Create a JPEG file with optional EXIF data."""
    img = Image.new("RGB", size, color=color)
    path = str(tmp_path / name)
    kwargs: dict = {"format": "JPEG"}
    if exif_dict is not None:
        kwargs["exif"] = piexif.dump(exif_dict)
    img.save(path, **kwargs)
    return path


def _make_png(
    tmp_path,
    name: str = "photo.png",
    size: tuple[int, int] = (200, 100),
    color: str = "blue",
    mode: str = "RGB",
) -> str:
    """Create a PNG file."""
    img = Image.new(mode, size, color=color)
    path = str(tmp_path / name)
    img.save(path, format="PNG")
    return path


def _make_rgba_png(
    tmp_path,
    name: str = "transparent.png",
    size: tuple[int, int] = (64, 64),
    has_transparency: bool = True,
) -> str:
    """Create an RGBA PNG.

    When *has_transparency* is ``True``, some pixels have alpha < 255.
    When ``False``, all pixels are fully opaque (alpha == 255).
    """
    img = Image.new("RGBA", size, color=(255, 0, 0, 255))
    if has_transparency:
        # Make the top-left quarter semi-transparent.
        for x in range(size[0] // 2):
            for y in range(size[1] // 2):
                img.putpixel((x, y), (255, 0, 0, 128))
    path = str(tmp_path / name)
    img.save(path, format="PNG")
    return path


def _build_exif(
    orientation: int | None = None,
    make: str | None = None,
    model: str | None = None,
    date_original: str | None = None,
    gps: bool = False,
) -> dict:
    """Build a piexif-compatible EXIF dictionary."""
    zeroth = {}
    exif_ifd = {}
    gps_ifd = {}

    if orientation is not None:
        zeroth[piexif.ImageIFD.Orientation] = orientation
    if make is not None:
        zeroth[piexif.ImageIFD.Make] = make.encode()
    if model is not None:
        zeroth[piexif.ImageIFD.Model] = model.encode()
    if date_original is not None:
        exif_ifd[piexif.ExifIFD.DateTimeOriginal] = date_original.encode()
    if gps:
        gps_ifd[piexif.GPSIFD.GPSLatitudeRef] = b"N"
        gps_ifd[piexif.GPSIFD.GPSLatitude] = ((40, 1), (44, 1), (0, 1))
        gps_ifd[piexif.GPSIFD.GPSLongitudeRef] = b"W"
        gps_ifd[piexif.GPSIFD.GPSLongitude] = ((73, 1), (59, 1), (0, 1))

    return {"0th": zeroth, "Exif": exif_ifd, "GPS": gps_ifd, "1st": {}, "thumbnail": None}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def config(tmp_path):
    return ConfigManager(config_dir=str(tmp_path / "cfg"))


@pytest.fixture()
def proc(config):
    return ImageProcessor(config)


# ===========================================================================
# 1.  copy_original
# ===========================================================================

class TestCopyOriginal:
    """Atomic copy of original file."""

    def test_copies_file(self, proc, tmp_path):
        src = _make_jpeg(tmp_path, "src.jpg")
        dst = str(tmp_path / "out" / "copy.jpg")
        result = proc.copy_original(src, dst)
        assert result is True
        assert os.path.exists(dst)
        assert os.path.getsize(dst) == os.path.getsize(src)

    def test_creates_parent_dirs(self, proc, tmp_path):
        src = _make_jpeg(tmp_path, "src.jpg")
        dst = str(tmp_path / "a" / "b" / "c" / "copy.jpg")
        proc.copy_original(src, dst)
        assert os.path.exists(dst)

    def test_preserves_content(self, proc, tmp_path):
        src = _make_jpeg(tmp_path, "src.jpg")
        dst = str(tmp_path / "copy.jpg")
        proc.copy_original(src, dst)
        assert open(src, "rb").read() == open(dst, "rb").read()


# ===========================================================================
# 2.  JPEG export from standard image
# ===========================================================================

class TestJpegExport:
    """generate_export produces a valid JPEG."""

    def test_basic_export(self, proc, tmp_path):
        src = _make_jpeg(tmp_path, "src.jpg")
        export = str(tmp_path / "export.jpg")
        result = proc.generate_export(src, export)

        assert result.success is True
        assert result.format == "jpeg"
        assert os.path.exists(result.export_path)
        assert result.export_path.endswith(".jpg")
        assert result.transparency_preserved is False

    def test_export_from_png(self, proc, tmp_path):
        """Non-JPEG input should still produce a JPEG export."""
        src = _make_png(tmp_path, "src.png")
        export = str(tmp_path / "export.jpg")
        result = proc.generate_export(src, export)

        assert result.success is True
        assert result.format == "jpeg"
        # Verify the output is actually JPEG
        with open(result.export_path, "rb") as f:
            assert f.read(2) == b"\xff\xd8"  # JPEG magic bytes

    def test_export_from_bmp(self, proc, tmp_path):
        img = Image.new("RGB", (100, 100), color="green")
        src = str(tmp_path / "src.bmp")
        img.save(src, format="BMP")
        export = str(tmp_path / "export.jpg")
        result = proc.generate_export(src, export)
        assert result.success is True
        assert result.format == "jpeg"

    def test_export_result_sizes(self, proc, tmp_path):
        src = _make_jpeg(tmp_path, "src.jpg", size=(300, 200))
        export = str(tmp_path / "export.jpg")
        result = proc.generate_export(src, export)
        assert result.original_size == (300, 200)
        assert result.export_size == (300, 200)  # no downscale
        assert result.was_downscaled is False

    def test_unsupported_format_raises(self, proc, tmp_path):
        bad = str(tmp_path / "bad.xyz")
        with open(bad, "wb") as f:
            f.write(b"not-an-image-format-at-all")
        with pytest.raises(UnsupportedFormatError):
            proc.generate_export(bad, str(tmp_path / "export.jpg"))


# ===========================================================================
# 3.  Transparency detection
# ===========================================================================

class TestTransparency:
    """RGBA images with real transparency → PNG; fully opaque → JPEG."""

    def test_real_transparency_saved_as_png(self, proc, tmp_path):
        src = _make_rgba_png(tmp_path, "trans.png", has_transparency=True)
        export = str(tmp_path / "export.jpg")
        result = proc.generate_export(src, export)

        assert result.success is True
        assert result.format == "png"
        assert result.transparency_preserved is True
        assert result.export_path.endswith(".png")

    def test_opaque_rgba_converted_to_jpeg(self, proc, tmp_path):
        src = _make_rgba_png(tmp_path, "opaque.png", has_transparency=False)
        export = str(tmp_path / "export.jpg")
        result = proc.generate_export(src, export)

        assert result.success is True
        assert result.format == "jpeg"
        assert result.transparency_preserved is False
        assert result.export_path.endswith(".jpg")

    def test_has_real_transparency_true(self, proc, tmp_path):
        img = Image.new("RGBA", (10, 10), color=(255, 0, 0, 128))
        assert proc._has_real_transparency(img) is True

    def test_has_real_transparency_false(self, proc, tmp_path):
        img = Image.new("RGBA", (10, 10), color=(255, 0, 0, 255))
        assert proc._has_real_transparency(img) is False

    def test_has_real_transparency_rgb_always_false(self, proc):
        img = Image.new("RGB", (10, 10), color="red")
        assert proc._has_real_transparency(img) is False

    def test_single_transparent_pixel(self, proc, tmp_path):
        """Even a single pixel with alpha < 255 counts."""
        img = Image.new("RGBA", (100, 100), color=(255, 0, 0, 255))
        img.putpixel((50, 50), (255, 0, 0, 0))
        assert proc._has_real_transparency(img) is True


# ===========================================================================
# 4.  Auto-rotation
# ===========================================================================

class TestAutoRotation:
    """EXIF Orientation tag physically rotates the export."""

    def _create_asymmetric_image(self, tmp_path, orientation: int) -> str:
        """Create a 200×100 JPEG with a specific EXIF orientation."""
        exif = _build_exif(orientation=orientation)
        return _make_jpeg(tmp_path, f"orient_{orientation}.jpg", size=(200, 100), exif_dict=exif)

    def test_orientation_1_no_rotation(self, proc, tmp_path):
        src = self._create_asymmetric_image(tmp_path, 1)
        export = str(tmp_path / "export.jpg")
        exif_r = ExifResult(orientation=1)
        result = proc.generate_export(src, export, exif_result=exif_r)
        assert result.was_rotated is False
        assert result.export_size == (200, 100)

    def test_orientation_3_rotate_180(self, proc, tmp_path):
        src = self._create_asymmetric_image(tmp_path, 3)
        export = str(tmp_path / "export.jpg")
        exif_r = ExifResult(orientation=3)
        result = proc.generate_export(src, export, exif_result=exif_r)
        assert result.was_rotated is True
        # 180° rotation preserves dimensions.
        assert result.export_size == (200, 100)

    def test_orientation_6_rotate_cw90(self, proc, tmp_path):
        """Orientation 6 = 90° CW → width/height swap."""
        src = self._create_asymmetric_image(tmp_path, 6)
        export = str(tmp_path / "export.jpg")
        exif_r = ExifResult(orientation=6)
        result = proc.generate_export(src, export, exif_result=exif_r)
        assert result.was_rotated is True
        assert result.export_size == (100, 200)

    def test_orientation_8_rotate_cw270(self, proc, tmp_path):
        """Orientation 8 = 270° CW → width/height swap."""
        src = self._create_asymmetric_image(tmp_path, 8)
        export = str(tmp_path / "export.jpg")
        exif_r = ExifResult(orientation=8)
        result = proc.generate_export(src, export, exif_result=exif_r)
        assert result.was_rotated is True
        assert result.export_size == (100, 200)

    def test_orientation_2_mirrored(self, proc, tmp_path):
        src = self._create_asymmetric_image(tmp_path, 2)
        export = str(tmp_path / "export.jpg")
        exif_r = ExifResult(orientation=2)
        result = proc.generate_export(src, export, exif_result=exif_r)
        assert result.was_rotated is True
        assert result.export_size == (200, 100)

    def test_orientation_5_mirrored_and_rotated(self, proc, tmp_path):
        src = self._create_asymmetric_image(tmp_path, 5)
        export = str(tmp_path / "export.jpg")
        exif_r = ExifResult(orientation=5)
        result = proc.generate_export(src, export, exif_result=exif_r)
        assert result.was_rotated is True
        # Mirror + 90 → swaps dimensions
        assert result.export_size == (100, 200)

    def test_no_exif_no_rotation(self, proc, tmp_path):
        src = _make_jpeg(tmp_path, "no_exif.jpg", size=(200, 100))
        export = str(tmp_path / "export.jpg")
        result = proc.generate_export(src, export)
        assert result.was_rotated is False
        assert result.export_size == (200, 100)

    def test_orientation_none_no_rotation(self, proc, tmp_path):
        src = _make_jpeg(tmp_path, "none_orient.jpg", size=(200, 100))
        export = str(tmp_path / "export.jpg")
        exif_r = ExifResult(orientation=None)
        result = proc.generate_export(src, export, exif_result=exif_r)
        assert result.was_rotated is False

    def test_pixel_content_actually_rotated(self, proc, tmp_path):
        """Verify rotation changes actual pixel layout, not just metadata."""
        # Create a 4×2 image where top-left pixel is distinct.
        img = Image.new("RGB", (4, 2), color=(0, 0, 0))
        img.putpixel((0, 0), (255, 0, 0))  # Red at top-left
        src = str(tmp_path / "marker.jpg")
        img.save(src, format="JPEG", quality=100, subsampling=0)

        export = str(tmp_path / "rotated.jpg")
        exif_r = ExifResult(orientation=6)  # 90° CW
        result = proc.generate_export(src, export, exif_result=exif_r)

        rotated = Image.open(result.export_path)
        # After 90° CW: (0,0) should now be at (w-1, 0) → pixel at
        # bottom-left of original.  Dimensions swap to (2, 4).
        assert rotated.size == (2, 4)


# ===========================================================================
# 5.  Downscaling
# ===========================================================================

class TestDownscaling:
    """Images larger than MAX_RESOLUTION are fitted into the bounding box."""

    def test_oversized_width(self, proc, tmp_path):
        """Width > 3840 → downscaled."""
        src = _make_jpeg(tmp_path, "wide.jpg", size=(7680, 2160))
        export = str(tmp_path / "export.jpg")
        result = proc.generate_export(src, export)

        assert result.was_downscaled is True
        w, h = result.export_size
        assert w <= 3840
        assert h <= 2160

    def test_oversized_height(self, proc, tmp_path):
        """Height > 2160 → downscaled."""
        src = _make_jpeg(tmp_path, "tall.jpg", size=(3840, 4320))
        export = str(tmp_path / "export.jpg")
        result = proc.generate_export(src, export)

        assert result.was_downscaled is True
        w, h = result.export_size
        assert w <= 3840
        assert h <= 2160

    def test_both_oversized(self, proc, tmp_path):
        """Both dimensions oversized → fit within 3840×2160."""
        src = _make_jpeg(tmp_path, "huge.jpg", size=(8000, 6000))
        export = str(tmp_path / "export.jpg")
        result = proc.generate_export(src, export)

        assert result.was_downscaled is True
        w, h = result.export_size
        assert w <= 3840
        assert h <= 2160

    def test_preserves_aspect_ratio(self, proc, tmp_path):
        """Aspect ratio is preserved after downscale."""
        src = _make_jpeg(tmp_path, "ratio.jpg", size=(7680, 4320))  # 16:9
        export = str(tmp_path / "export.jpg")
        result = proc.generate_export(src, export)

        w, h = result.export_size
        # Original ratio = 16:9.  After fitting within 3840×2160,
        # the limiting dimension is height → w=3840, h=2160.
        assert w <= 3840
        assert h <= 2160
        # Check aspect ratio is approximately maintained.
        original_ratio = 7680 / 4320
        export_ratio = w / h
        assert abs(original_ratio - export_ratio) < 0.02

    def test_no_upscale(self, proc, tmp_path):
        """Image smaller than MAX_RESOLUTION is NOT upscaled."""
        src = _make_jpeg(tmp_path, "small.jpg", size=(640, 480))
        export = str(tmp_path / "export.jpg")
        result = proc.generate_export(src, export)

        assert result.was_downscaled is False
        assert result.export_size == (640, 480)

    def test_exact_max_not_downscaled(self, proc, tmp_path):
        """Image exactly at MAX_RESOLUTION is not downscaled."""
        src = _make_jpeg(tmp_path, "exact.jpg", size=(3840, 2160))
        export = str(tmp_path / "export.jpg")
        result = proc.generate_export(src, export)

        assert result.was_downscaled is False
        assert result.export_size == (3840, 2160)

    def test_fit_within_method(self, proc):
        """Direct _fit_within unit test."""
        img = Image.new("RGB", (8000, 4000))
        resized, downscaled = proc._fit_within(img, 3840, 2160)
        assert downscaled is True
        w, h = resized.size
        assert w <= 3840
        assert h <= 2160

    def test_fit_within_no_upscale(self, proc):
        img = Image.new("RGB", (100, 50))
        resized, downscaled = proc._fit_within(img, 3840, 2160)
        assert downscaled is False
        assert resized.size == (100, 50)

    def test_custom_max_resolution(self, tmp_path):
        """Config override for max_resolution is respected."""
        config = ConfigManager(config_dir=str(tmp_path / "cfg"))
        config.set("max_resolution", [1920, 1080])
        proc = ImageProcessor(config)

        src = _make_jpeg(tmp_path, "big.jpg", size=(3840, 2160))
        export = str(tmp_path / "export.jpg")
        result = proc.generate_export(src, export)

        assert result.was_downscaled is True
        w, h = result.export_size
        assert w <= 1920
        assert h <= 1080


# ===========================================================================
# 6.  EXIF preservation
# ===========================================================================

class TestExifPreservation:
    """Export preserves camera, date, and GPS EXIF data."""

    def test_exif_preserved_in_export(self, proc, tmp_path):
        """Camera make, model, and date survive the export."""
        exif = _build_exif(
            make="Canon",
            model="EOS R5",
            date_original="2024:03:15 14:30:00",
        )
        src = _make_jpeg(tmp_path, "exif.jpg", exif_dict=exif)
        export = str(tmp_path / "export.jpg")
        result = proc.generate_export(src, export)

        assert result.success is True

        # Read EXIF from the exported file.
        exported_exif = piexif.load(result.export_path)
        ifd0 = exported_exif.get("0th", {})
        exif_ifd = exported_exif.get("Exif", {})

        make_out = ifd0.get(piexif.ImageIFD.Make, b"").decode().strip("\x00")
        model_out = ifd0.get(piexif.ImageIFD.Model, b"").decode().strip("\x00")
        date_out = exif_ifd.get(piexif.ExifIFD.DateTimeOriginal, b"").decode().strip("\x00")

        assert "Canon" in make_out
        assert "EOS R5" in model_out
        assert date_out == "2024:03:15 14:30:00"

    def test_gps_preserved(self, proc, tmp_path):
        """GPS coordinates survive the export."""
        exif = _build_exif(gps=True)
        src = _make_jpeg(tmp_path, "gps.jpg", exif_dict=exif)
        export = str(tmp_path / "export.jpg")
        result = proc.generate_export(src, export)

        exported_exif = piexif.load(result.export_path)
        gps_ifd = exported_exif.get("GPS", {})
        assert piexif.GPSIFD.GPSLatitude in gps_ifd
        assert piexif.GPSIFD.GPSLongitude in gps_ifd

    def test_thumbnail_stripped(self, proc, tmp_path):
        """IFD1 thumbnail data is stripped from the export."""
        # Create EXIF with a thumbnail.
        thumb = Image.new("RGB", (160, 120), color="gray")
        buf = BytesIO()
        thumb.save(buf, format="JPEG")
        thumb_bytes = buf.getvalue()

        exif_dict = _build_exif(make="Nikon", model="Z8")
        exif_dict["1st"] = {piexif.ImageIFD.Compression: 6}
        exif_dict["thumbnail"] = thumb_bytes

        src = _make_jpeg(tmp_path, "thumb.jpg", exif_dict=exif_dict)

        # Verify source has thumbnail.
        source_exif = piexif.load(src)
        assert source_exif.get("thumbnail") is not None and len(source_exif["thumbnail"]) > 0

        export = str(tmp_path / "export.jpg")
        result = proc.generate_export(src, export)

        # Verify export has no thumbnail.
        exported_exif = piexif.load(result.export_path)
        assert not exported_exif.get("thumbnail")
        assert not exported_exif.get("1st")

    def test_no_exif_warning_not_error(self, proc, tmp_path):
        """Image without EXIF still exports successfully."""
        img = Image.new("RGB", (200, 100), color="red")
        src = str(tmp_path / "no_exif.bmp")
        img.save(src, format="BMP")
        export = str(tmp_path / "export.jpg")
        result = proc.generate_export(src, export)
        assert result.success is True
        assert result.format == "jpeg"


# ===========================================================================
# 7.  JPEG quality from config
# ===========================================================================

class TestJpegQuality:

    def test_default_quality(self, proc, tmp_path):
        """Default quality is 90."""
        assert proc.config.jpeg_quality == 90

    def test_custom_quality_smaller_file(self, tmp_path):
        """Lower quality should produce a smaller file."""
        config_low = ConfigManager(config_dir=str(tmp_path / "cfg_low"))
        config_low.set("jpeg_quality", 20)

        config_high = ConfigManager(config_dir=str(tmp_path / "cfg_high"))
        config_high.set("jpeg_quality", 95)

        proc_low = ImageProcessor(config_low)
        proc_high = ImageProcessor(config_high)

        # Create a complex image (gradient) so quality makes a size difference.
        import numpy as np
        arr = np.random.randint(0, 255, (500, 500, 3), dtype=np.uint8)
        img = Image.fromarray(arr)
        src = str(tmp_path / "random.png")
        img.save(src, format="PNG")

        exp_low = str(tmp_path / "low.jpg")
        exp_high = str(tmp_path / "high.jpg")
        proc_low.generate_export(src, exp_low)
        proc_high.generate_export(src, exp_high)

        assert os.path.getsize(exp_low) < os.path.getsize(exp_high)


# ===========================================================================
# 8.  _open_image
# ===========================================================================

class TestOpenImage:

    def test_open_jpeg(self, proc, tmp_path):
        src = _make_jpeg(tmp_path, "test.jpg")
        img = proc._open_image(src)
        assert isinstance(img, Image.Image)
        assert img.size == (200, 100)

    def test_open_png(self, proc, tmp_path):
        src = _make_png(tmp_path, "test.png")
        img = proc._open_image(src)
        assert isinstance(img, Image.Image)

    def test_open_corrupt_raises(self, proc, tmp_path):
        bad = str(tmp_path / "bad.jpg")
        with open(bad, "wb") as f:
            f.write(b"definitely not a jpeg")
        with pytest.raises(UnsupportedFormatError):
            proc._open_image(bad)

    def test_open_raw_without_rawpy(self, proc, tmp_path):
        """When rawpy is unavailable, opening a RAW file raises UnsupportedFormatError."""
        fake_raw = str(tmp_path / "test.cr2")
        with open(fake_raw, "wb") as f:
            f.write(b"\x00" * 100)

        import sortique.engine.processors.image_processor as mod
        original = mod._RAWPY_AVAILABLE
        try:
            mod._RAWPY_AVAILABLE = False
            with pytest.raises(UnsupportedFormatError, match="rawpy"):
                proc._open_image(fake_raw)
        finally:
            mod._RAWPY_AVAILABLE = original

    def test_open_heic_without_pillow_heif(self, proc, tmp_path):
        """When pillow-heif is unavailable, opening a HEIC file raises UnsupportedFormatError."""
        fake_heic = str(tmp_path / "test.heic")
        with open(fake_heic, "wb") as f:
            f.write(b"\x00" * 100)

        import sortique.engine.processors.image_processor as mod
        original = mod._HEIF_AVAILABLE
        try:
            mod._HEIF_AVAILABLE = False
            with pytest.raises(UnsupportedFormatError, match="pillow-heif"):
                proc._open_image(fake_heic)
        finally:
            mod._HEIF_AVAILABLE = original


# ===========================================================================
# 9.  _strip_thumbnail
# ===========================================================================

class TestStripThumbnail:

    def test_strips_thumbnail(self, proc):
        thumb = Image.new("RGB", (80, 60), color="gray")
        buf = BytesIO()
        thumb.save(buf, format="JPEG")
        thumb_bytes = buf.getvalue()

        exif_dict = {
            "0th": {piexif.ImageIFD.Make: b"Test"},
            "Exif": {},
            "GPS": {},
            "1st": {piexif.ImageIFD.Compression: 6},
            "thumbnail": thumb_bytes,
        }
        exif_bytes = piexif.dump(exif_dict)

        stripped = proc._strip_thumbnail(exif_bytes)
        result = piexif.load(stripped)
        assert not result.get("thumbnail")
        # IFD0 data should survive.
        assert result["0th"].get(piexif.ImageIFD.Make) == b"Test"

    def test_handles_no_thumbnail_gracefully(self, proc):
        exif_dict = {
            "0th": {piexif.ImageIFD.Make: b"Test"},
            "Exif": {},
            "GPS": {},
            "1st": {},
            "thumbnail": None,
        }
        exif_bytes = piexif.dump(exif_dict)
        stripped = proc._strip_thumbnail(exif_bytes)
        result = piexif.load(stripped)
        assert result["0th"].get(piexif.ImageIFD.Make) == b"Test"

    def test_invalid_bytes_returned_as_is(self, proc):
        bad = b"not-exif-data"
        assert proc._strip_thumbnail(bad) == bad


# ===========================================================================
# 10.  ExportResult dataclass
# ===========================================================================

class TestExportResult:

    def test_fields(self):
        r = ExportResult(
            success=True,
            export_path="/dst/photo.jpg",
            format="jpeg",
            original_size=(4000, 3000),
            export_size=(3840, 2880),
            was_downscaled=True,
            was_rotated=False,
            transparency_preserved=False,
            warnings=["minor issue"],
        )
        assert r.success is True
        assert r.format == "jpeg"
        assert r.was_downscaled is True
        assert r.warnings == ["minor issue"]

    def test_default_warnings_empty(self):
        r = ExportResult(
            success=True,
            export_path="",
            format="jpeg",
            original_size=(100, 100),
            export_size=(100, 100),
            was_downscaled=False,
            was_rotated=False,
            transparency_preserved=False,
        )
        assert r.warnings == []


# ===========================================================================
# 11.  Edge cases
# ===========================================================================

class TestEdgeCases:

    def test_grayscale_to_jpeg(self, proc, tmp_path):
        """Grayscale (L mode) image is converted to RGB for JPEG export."""
        img = Image.new("L", (100, 100), color=128)
        src = str(tmp_path / "gray.png")
        img.save(src, format="PNG")
        export = str(tmp_path / "export.jpg")
        result = proc.generate_export(src, export)
        assert result.success is True
        assert result.format == "jpeg"

    def test_cmyk_to_jpeg(self, proc, tmp_path):
        """CMYK image is converted to RGB for JPEG export."""
        img = Image.new("CMYK", (100, 100), color=(0, 0, 0, 0))
        src = str(tmp_path / "cmyk.tiff")
        img.save(src, format="TIFF")
        export = str(tmp_path / "export.jpg")
        result = proc.generate_export(src, export)
        assert result.success is True
        assert result.format == "jpeg"

    def test_rotation_then_downscale(self, proc, tmp_path):
        """Rotation is applied before downscale — rotated dimensions are used."""
        # 5000×100 rotated 90° CW becomes 100×5000.  Only the 5000
        # height exceeds max (2160), so it should be downscaled.
        src = _make_jpeg(tmp_path, "rotscale.jpg", size=(5000, 100))
        export = str(tmp_path / "export.jpg")
        exif_r = ExifResult(orientation=6)  # 90° CW
        result = proc.generate_export(src, export, exif_result=exif_r)

        assert result.was_rotated is True
        assert result.was_downscaled is True
        w, h = result.export_size
        assert w <= 3840
        assert h <= 2160
