#!/usr/bin/env bash
# =========================================================================
# Sortique — Linux build script
# Creates a virtual environment, installs dependencies, and builds the
# single-file executable with PyInstaller.
#
# Usage:  bash scripts/build_linux.sh          (run from repo root)
# =========================================================================
set -euo pipefail

echo "==================================================="
echo " Sortique Build Script — Linux"
echo "==================================================="
echo

# ------------------------------------------------------------------
# 1. Locate Python
# ------------------------------------------------------------------
PYTHON="${PYTHON:-python3}"

if ! command -v "$PYTHON" &>/dev/null; then
    echo "ERROR: $PYTHON not found on PATH."
    echo "Install Python 3.11+:  sudo apt install python3 python3-venv  (Debian/Ubuntu)"
    echo "                       sudo dnf install python3               (Fedora)"
    exit 1
fi

PYVER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PYMAJOR=$("$PYTHON" -c "import sys; print(sys.version_info.major)")
PYMINOR=$("$PYTHON" -c "import sys; print(sys.version_info.minor)")

if [ "$PYMAJOR" -lt 3 ] || { [ "$PYMAJOR" -eq 3 ] && [ "$PYMINOR" -lt 11 ]; }; then
    echo "ERROR: Python 3.11+ required, found $PYVER"
    exit 1
fi
echo "[OK] Python $PYVER"

# ------------------------------------------------------------------
# 2. Check system dependencies
# ------------------------------------------------------------------
# python3-venv is a separate package on Debian/Ubuntu
if ! "$PYTHON" -m venv --help &>/dev/null; then
    echo "ERROR: python3-venv module not available."
    echo "Install it:  sudo apt install python3-venv  (Debian/Ubuntu)"
    exit 1
fi

# libmagic is needed for python-magic
if ! ldconfig -p 2>/dev/null | grep -q libmagic; then
    echo "WARNING: libmagic not found. Install it for file-type detection:"
    echo "  sudo apt install libmagic1       (Debian/Ubuntu)"
    echo "  sudo dnf install file-libs       (Fedora)"
fi

# ------------------------------------------------------------------
# 3. Create virtual environment
# ------------------------------------------------------------------
VENV_DIR=".venv"

if [ -f "$VENV_DIR/bin/activate" ]; then
    echo "[OK] Virtual environment already exists at $VENV_DIR"
else
    echo "[..] Creating virtual environment..."
    "$PYTHON" -m venv "$VENV_DIR"
    echo "[OK] Virtual environment created"
fi

# ------------------------------------------------------------------
# 4. Activate virtual environment
# ------------------------------------------------------------------
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
echo "[OK] Virtual environment activated"

# ------------------------------------------------------------------
# 5. Upgrade pip
# ------------------------------------------------------------------
echo "[..] Upgrading pip..."
python -m pip install --upgrade pip --quiet || echo "WARNING: pip upgrade failed, continuing..."

# ------------------------------------------------------------------
# 6. Install dependencies
# ------------------------------------------------------------------
echo "[..] Installing dependencies..."

# Install core requirements
python -m pip install --quiet -r requirements.txt 2>/dev/null || true

# Install PyInstaller (build tool)
python -m pip install --quiet "pyinstaller>=6.0.0"

# Report optional packages
python -c "import rawpy" 2>/dev/null         || echo "WARNING: rawpy not available — RAW file support disabled"
python -c "import pillow_heif" 2>/dev/null   || echo "WARNING: pillow-heif not available — HEIC/HEIF support disabled"
python -c "import magic" 2>/dev/null         || echo "WARNING: python-magic not available — magic-byte detection limited"

# Verify critical packages
if ! python -c "import PySide6" 2>/dev/null; then
    echo "ERROR: PySide6 failed to install — cannot build GUI application."
    exit 1
fi
if ! python -c "import PIL" 2>/dev/null; then
    echo "ERROR: Pillow failed to install — cannot build application."
    exit 1
fi
echo "[OK] Dependencies installed"

# ------------------------------------------------------------------
# 7. Run tests (quick sanity check)
# ------------------------------------------------------------------
echo "[..] Running tests..."
python -m pytest tests/ -x -q --tb=line 2>/dev/null || echo "WARNING: Some tests failed. Build will continue."
echo "[OK] Tests complete"

# ------------------------------------------------------------------
# 8. Clean previous build artifacts
# ------------------------------------------------------------------
echo "[..] Cleaning previous build..."
rm -rf build dist
echo "[OK] Clean"

# ------------------------------------------------------------------
# 9. Build with PyInstaller
# ------------------------------------------------------------------
echo "[..] Building executable with PyInstaller..."
pyinstaller sortique.spec

if [ ! -f "dist/sortique" ]; then
    echo
    echo "==================================================="
    echo " BUILD FAILED"
    echo "==================================================="
    exit 1
fi

# ------------------------------------------------------------------
# 10. Report result
# ------------------------------------------------------------------
SIZE=$(du -sh "dist/sortique" | cut -f1)
echo
echo "==================================================="
echo " BUILD SUCCESSFUL"
echo " Output: dist/sortique ($SIZE)"
echo ""
echo " Optional — create an AppImage (requires appimagetool):"
echo "   mkdir -p AppDir/usr/bin"
echo "   cp dist/sortique AppDir/usr/bin/sortique"
echo "   cp resources/app_icon.svg AppDir/sortique.svg"
echo "   appimagetool AppDir dist/Sortique-x86_64.AppImage"
echo "==================================================="
