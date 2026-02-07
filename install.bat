@echo off
chcp 65001 >nul
title Installing Ocean Aquarium Control System

echo.
echo ╔══════════════════════════════════════════════════════════╗
echo ║     🌊 OCEAN AQUARIUM EQUIPMENT CONTROL SYSTEM 🌊        ║
echo ║              ONLINE INSTALLATION                          ║
echo ╠══════════════════════════════════════════════════════════╣
echo ║  This script will:                                        ║
echo ║  1. Create virtual environment (venv)                    ║
echo ║  2. Install all dependencies from PyPI                   ║
echo ╚══════════════════════════════════════════════════════════╝
echo.

cd /d "%~dp0"

REM Check Python
echo [1/4] Checking Python installation...
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found! Please install Python 3.10+
    echo Download from: https://www.python.org/downloads/
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

REM Install dependencies
echo [4/4] Installing dependencies...
pip install --upgrade pip
pip install -r requirements.txt

echo.
echo ══════════════════════════════════════════════════════════
echo ✅ Installation complete!
echo.
echo Next steps:
echo   1. Edit config.json with your device IP addresses
echo   2. Run start.bat to start the server
echo   3. Open http://localhost:8000 in your browser
echo ══════════════════════════════════════════════════════════
echo.
pause
