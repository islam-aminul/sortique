@echo off
setlocal enabledelayedexpansion
:: =========================================================================
:: Sortique — Windows build script
:: Creates a virtual environment, installs dependencies, and builds the
:: single-file executable with PyInstaller.
::
:: Usage:  scripts\build_windows.bat          (run from repo root)
:: =========================================================================

echo ===================================================
echo  Sortique Build Script — Windows
echo ===================================================
echo.

:: ------------------------------------------------------------------
:: 1. Locate Python
:: ------------------------------------------------------------------
where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo ERROR: python not found on PATH.
    echo Install Python 3.11+ from https://www.python.org/downloads/
    exit /b 1
)

:: Verify minimum Python version (3.11)
for /f "tokens=*" %%v in ('python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"') do set PYVER=%%v
for /f "tokens=1,2 delims=." %%a in ("%PYVER%") do (
    if %%a LSS 3 (
        echo ERROR: Python 3.11+ required, found %PYVER%
        exit /b 1
    )
    if %%a EQU 3 if %%b LSS 11 (
        echo ERROR: Python 3.11+ required, found %PYVER%
        exit /b 1
    )
)
echo [OK] Python %PYVER%

:: ------------------------------------------------------------------
:: 2. Create virtual environment
:: ------------------------------------------------------------------
set VENV_DIR=.venv

if exist "%VENV_DIR%\Scripts\activate.bat" (
    echo [OK] Virtual environment already exists at %VENV_DIR%
) else (
    echo [..] Creating virtual environment...
    python -m venv "%VENV_DIR%"
    if %ERRORLEVEL% neq 0 (
        echo ERROR: Failed to create virtual environment.
        exit /b 1
    )
    echo [OK] Virtual environment created
)

:: ------------------------------------------------------------------
:: 3. Activate virtual environment
:: ------------------------------------------------------------------
call "%VENV_DIR%\Scripts\activate.bat"
echo [OK] Virtual environment activated

:: ------------------------------------------------------------------
:: 4. Upgrade pip
:: ------------------------------------------------------------------
echo [..] Upgrading pip...
python -m pip install --upgrade pip --quiet
if %ERRORLEVEL% neq 0 (
    echo WARNING: pip upgrade failed, continuing anyway...
)

:: ------------------------------------------------------------------
:: 5. Install dependencies
:: ------------------------------------------------------------------
echo [..] Installing dependencies...

:: Install core requirements (skip packages that may not have ARM64 wheels)
python -m pip install --quiet -r requirements.txt 2>nul
set INSTALL_ERR=%ERRORLEVEL%

:: On Windows, python-magic needs the -bin variant
python -m pip install --quiet python-magic-bin>=0.4.14 2>nul

:: Install PyInstaller (build tool)
python -m pip install --quiet pyinstaller>=6.0.0
if %ERRORLEVEL% neq 0 (
    echo ERROR: Failed to install PyInstaller.
    exit /b 1
)

:: Report optional packages that may have failed
python -c "import rawpy" 2>nul || echo WARNING: rawpy not available — RAW file support disabled
python -c "import pillow_heif" 2>nul || echo WARNING: pillow-heif not available — HEIC/HEIF support disabled
python -c "import magic" 2>nul || echo WARNING: python-magic not available — magic-byte detection limited

:: Verify critical packages
python -c "import PySide6" 2>nul
if %ERRORLEVEL% neq 0 (
    echo ERROR: PySide6 failed to install — cannot build GUI application.
    exit /b 1
)
python -c "import PIL" 2>nul
if %ERRORLEVEL% neq 0 (
    echo ERROR: Pillow failed to install — cannot build application.
    exit /b 1
)
echo [OK] Dependencies installed

:: ------------------------------------------------------------------
:: 6. Run tests (quick sanity check)
:: ------------------------------------------------------------------
echo [..] Running tests...
python -m pytest tests/ -x -q --tb=line 2>nul
if %ERRORLEVEL% neq 0 (
    echo WARNING: Some tests failed. Build will continue.
)
echo [OK] Tests complete

:: ------------------------------------------------------------------
:: 7. Clean previous build artifacts
:: ------------------------------------------------------------------
echo [..] Cleaning previous build...
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist
echo [OK] Clean

:: ------------------------------------------------------------------
:: 8. Build with PyInstaller
:: ------------------------------------------------------------------
echo [..] Building executable with PyInstaller...
pyinstaller sortique.spec
if %ERRORLEVEL% neq 0 (
    echo.
    echo ===================================================
    echo  BUILD FAILED
    echo ===================================================
    exit /b 1
)

:: ------------------------------------------------------------------
:: 9. Verify output
:: ------------------------------------------------------------------
if not exist "dist\sortique.exe" (
    echo ERROR: Expected dist\sortique.exe not found.
    exit /b 1
)

for %%A in ("dist\sortique.exe") do set SIZE=%%~zA
set /a SIZE_MB=%SIZE% / 1048576
echo.
echo ===================================================
echo  BUILD SUCCESSFUL
echo  Output: dist\sortique.exe (%SIZE_MB% MB)
echo ===================================================
