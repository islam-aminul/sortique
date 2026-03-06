#!/usr/bin/env bash
# Build script for macOS
# Run from the repo root: bash scripts/build_macos.sh
set -euo pipefail

echo "Building Sortique for macOS..."

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
echo "Build complete: dist/Sortique.app"
echo "To create a distributable DMG:"
echo "  hdiutil create -volname Sortique -srcfolder dist/Sortique.app -ov -format UDZO dist/Sortique.dmg"
