@echo off
:: Build script for Windows
:: Run from the repo root: scripts\build_windows.bat

echo Building Sortique for Windows...

:: Check PyInstaller is available
where pyinstaller >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo ERROR: pyinstaller not found. Run: pip install pyinstaller
    exit /b 1
)

:: Clean previous build artifacts
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist

:: Run PyInstaller
pyinstaller sortique.spec

if %ERRORLEVEL% neq 0 (
    echo Build failed.
    exit /b 1
)

echo.
echo Build complete: dist\sortique.exe
