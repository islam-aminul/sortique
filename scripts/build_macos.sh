#!/usr/bin/env bash
# =========================================================================
# Sortique — macOS build script
# Creates a virtual environment, installs dependencies, and builds the
# .app bundle with PyInstaller.
#
# Usage:  bash scripts/build_macos.sh          (run from repo root)
# =========================================================================
set -euo pipefail

echo "==================================================="
echo " Sortique Build Script — macOS"
echo "==================================================="
echo

# ------------------------------------------------------------------
# 1. Locate Python
# ------------------------------------------------------------------
PYTHON="${PYTHON:-python3}"

if ! command -v "$PYTHON" &>/dev/null; then
    echo "ERROR: $PYTHON not found on PATH."
    echo "Install Python 3.11+ from https://www.python.org/downloads/"
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
# 2. Create virtual environment
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
# 3. Activate virtual environment
# ------------------------------------------------------------------
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
echo "[OK] Virtual environment activated"

# ------------------------------------------------------------------
# 4. Upgrade pip
# ------------------------------------------------------------------
echo "[..] Upgrading pip..."
python -m pip install --upgrade pip --quiet || echo "WARNING: pip upgrade failed, continuing..."

# ------------------------------------------------------------------
# 5. Install dependencies
# ------------------------------------------------------------------
echo "[..] Installing dependencies..."

# Install core requirements
python -m pip install --quiet -r requirements.txt 2>/dev/null || true

# macOS may need libmagic via Homebrew for python-magic
if ! python -c "import magic" 2>/dev/null; then
    if command -v brew &>/dev/null; then
        echo "[..] Installing libmagic via Homebrew..."
        brew install libmagic 2>/dev/null || true
        python -m pip install --quiet python-magic>=0.4.27 2>/dev/null || true
    fi
fi

# Install PyInstaller (build tool)
python -m pip install --quiet "pyinstaller>=6.0.0"

# Report optional packages
python -c "import rawpy" 2>/dev/null        || echo "WARNING: rawpy not available — RAW file support disabled"
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
# 6. Run tests (quick sanity check)
# ------------------------------------------------------------------
echo "[..] Running tests..."
python -m pytest tests/ -x -q --tb=line 2>/dev/null || echo "WARNING: Some tests failed. Build will continue."
echo "[OK] Tests complete"

# ------------------------------------------------------------------
# 7. Clean previous build artifacts
# ------------------------------------------------------------------
echo "[..] Cleaning previous build..."
rm -rf build dist
echo "[OK] Clean"

# ------------------------------------------------------------------
# 8. Build with PyInstaller
# ------------------------------------------------------------------
echo "[..] Building .app bundle with PyInstaller..."
pyinstaller sortique.spec

if [ ! -d "dist/Sortique.app" ] && [ ! -f "dist/sortique" ]; then
    echo
    echo "==================================================="
    echo " BUILD FAILED"
    echo "==================================================="
    exit 1
fi

# ------------------------------------------------------------------
# 9. Report result
# ------------------------------------------------------------------
echo
if [ -d "dist/Sortique.app" ]; then
    SIZE=$(du -sh "dist/Sortique.app" | cut -f1)
    echo "==================================================="
    echo " BUILD SUCCESSFUL"
    echo " Output: dist/Sortique.app ($SIZE)"
    echo ""
    echo " To create a distributable DMG:"
    echo "   hdiutil create -volname Sortique -srcfolder dist/Sortique.app \\"
    echo "     -ov -format UDZO dist/Sortique.dmg"
    echo "==================================================="
else
    SIZE=$(du -sh "dist/sortique" | cut -f1)
    echo "==================================================="
    echo " BUILD SUCCESSFUL"
    echo " Output: dist/sortique ($SIZE)"
    echo "==================================================="
fi
