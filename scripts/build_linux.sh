#!/usr/bin/env bash
# Build script for Linux
# Run from the repo root: bash scripts/build_linux.sh
set -euo pipefail

echo "Building Sortique for Linux..."

# Check PyInstaller is available
if ! command -v pyinstaller &>/dev/null; then
    echo "ERROR: pyinstaller not found. Run: pip install pyinstaller" >&2
    exit 1
fi

# Clean previous build artifacts
rm -rf build dist

# Run PyInstaller
pyinstaller sortique.spec

echo ""
echo "Build complete: dist/sortique"

# Optionally wrap in an AppImage (requires appimagetool):
# if command -v appimagetool &>/dev/null; then
#     mkdir -p AppDir/usr/bin
#     cp dist/sortique AppDir/usr/bin/sortique
#     cp resources/app_icon.svg AppDir/sortique.svg
#     appimagetool AppDir dist/Sortique-x86_64.AppImage
#     echo "AppImage: dist/Sortique-x86_64.AppImage"
# fi
