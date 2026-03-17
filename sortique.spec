# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for Sortique.
#
# Run from the repo root (the directory that contains this file AND is the
# sortique Python package):
#
#   pyinstaller sortique.spec
#
# The repo root IS the sortique package directory.  We add the parent directory
# to pathex so that "import sortique" resolves correctly during analysis.

import sys
import platform
from pathlib import Path

block_cipher = None

repo_root = Path(SPECPATH)        # /…/sortique/
parent_dir = str(repo_root.parent)  # /…/  (where the `sortique` package lives)

# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------
IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"

_os_name = "Windows" if IS_WINDOWS else ("macOS" if IS_MACOS else "Linux")
_arch    = platform.machine()          # arm64 | x86_64 | AMD64
EXE_NAME = f"sortique-{_os_name}-{_arch}"
APP_NAME = f"Sortique-{_os_name}-{_arch}.app"

icon_file = None
if IS_WINDOWS:
    _ico = repo_root / "resources" / "app_icon.ico"
    if _ico.exists():
        icon_file = str(_ico)
elif IS_MACOS:
    _icns = repo_root / "resources" / "app_icon.icns"
    if _icns.exists():
        icon_file = str(_icns)
# Linux uses the SVG at runtime; no icon_file needed for the binary itself.

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
a = Analysis(
    ["__main__.py"],       # entry point — repo root IS the package
    pathex=[parent_dir],   # so `import sortique` resolves from parent
    binaries=[],
    datas=[
        # Bundle the defaults config and icon so they're accessible at runtime.
        ("config/defaults.json", "config"),
        ("resources", "resources"),
    ],
    hiddenimports=[
        # PySide6 modules not always auto-detected
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "PySide6.QtSvg",
        "PySide6.QtSvgWidgets",
        "PySide6.QtXml",
        # Pillow image plugins
        "PIL.JpegImagePlugin",
        "PIL.PngImagePlugin",
        "PIL.WebPImagePlugin",
        "PIL.TiffImagePlugin",
        "PIL.GifImagePlugin",
        "PIL.BmpImagePlugin",
        "PIL.Image",
        "PIL.ExifTags",
        # pillow-heif
        "pillow_heif",
        # rawpy
        "rawpy",
        "rawpy._rawpy",
        # imagehash
        "imagehash",
        # mutagen audio formats
        "mutagen.mp3",
        "mutagen.mp4",
        "mutagen.flac",
        "mutagen.ogg",
        "mutagen.oggvorbis",
        "mutagen.id3",
        # musicbrainz
        "musicbrainzngs",
        # magic
        "magic",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ---------------------------------------------------------------------------
# Single-file executable
# ---------------------------------------------------------------------------
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name=EXE_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,            # enable terminal output for CLI mode
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_file,
)

# ---------------------------------------------------------------------------
# macOS .app bundle
# ---------------------------------------------------------------------------
if IS_MACOS:
    app = BUNDLE(
        exe,
        name=APP_NAME,
        icon=icon_file,
        bundle_identifier="com.sortique.app",
        info_plist={
            "CFBundleDisplayName": "Sortique",
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1.0.0",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,  # allow dark mode
        },
    )
