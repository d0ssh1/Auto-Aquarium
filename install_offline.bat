@echo off
chcp 65001 >nul
title Offline Installation - Ocean Aquarium Control System

echo.
echo ╔══════════════════════════════════════════════════════════╗
echo ║     🌊 OCEAN AQUARIUM EQUIPMENT CONTROL SYSTEM 🌊        ║
echo ║              OFFLINE INSTALLATION                         ║
echo ╠══════════════════════════════════════════════════════════╣
echo ║  This script installs from local 'packages' folder.      ║
echo ║  No internet connection required!                         ║
echo ╚══════════════════════════════════════════════════════════╝
echo.

cd /d "%~dp0"

REM Check if packages folder exists
if not exist "packages" (
    echo ERROR: 'packages' folder not found!
    echo.
    echo You need to run 'download_packages.bat' on a computer
    echo with internet access first, then copy the entire
    echo Ocean folder to this computer.
    echo.
    pause
    exit /b 1
)

REM Check Python
echo [1/4] Checking Python installation...
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found! Please install Python 3.10+
    echo.
    echo If Python is installed but not in PATH:
    echo 1. Run installer again
    echo 2. Check "Add Python to PATH"
    echo.
    pause
    exit /b 1
)

python --version
echo.

REM Create venv
echo [2/4] Creating virtual environment...
if not exist "venv" (
    python -m venv venv
    echo Virtual environment created.
) else (
    echo Virtual environment already exists.
)
echo.

REM Activate venv
echo [3/4] Activating virtual environment...
call venv\Scripts\activate.bat
echo.

REM Install from local packages
echo [4/4] Installing packages from local folder...
pip install --no-index --find-links=packages -r requirements.txt

if errorlevel 1 (
    echo.
    echo WARNING: Some packages may have failed to install.
    echo Trying alternative installation method...
    for %%f in (packages\*.whl) do pip install "%%f" --no-deps 2>nul
    pip install -r requirements.txt --no-index --find-links=packages 2>nul
)

echo.
echo ══════════════════════════════════════════════════════════
echo ✅ Offline installation complete!
echo.
echo Next steps:
echo   1. Edit config.json with your device IP addresses
echo   2. Run start.bat to start the server
echo   3. Open http://localhost:8000 in your browser
echo ══════════════════════════════════════════════════════════
echo.
pause
