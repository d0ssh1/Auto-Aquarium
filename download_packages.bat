@echo off
chcp 65001 >nul
title Downloading Packages for Offline Installation

echo.
echo ╔══════════════════════════════════════════════════════════╗
echo ║     🌊 OCEAN AQUARIUM EQUIPMENT CONTROL SYSTEM 🌊        ║
echo ║         DOWNLOAD PACKAGES FOR OFFLINE INSTALL            ║
echo ╠══════════════════════════════════════════════════════════╣
echo ║  This script will download all packages as .whl files    ║
echo ║  to the 'packages' folder for offline installation.      ║
echo ║                                                           ║
echo ║  Run this on a computer with internet access!            ║
echo ╚══════════════════════════════════════════════════════════╝
echo.

cd /d "%~dp0"

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found! Please install Python 3.10+
    pause
    exit /b 1
)

REM Create packages folder
if not exist "packages" mkdir packages

echo.
echo Downloading packages to 'packages' folder...
echo This may take several minutes...
echo.

REM Download all packages as wheel files
pip download -r requirements.txt -d packages --only-binary=:all: --python-version 3.10 --platform win_amd64

REM If binary not available, try without platform restriction
if errorlevel 1 (
    echo.
    echo Some packages need source download, retrying...
    pip download -r requirements.txt -d packages
)

echo.
echo ══════════════════════════════════════════════════════════
echo ✅ Packages downloaded to 'packages' folder!
echo.
echo Now copy the entire Ocean folder to the offline computer
echo and run 'install_offline.bat' there.
echo ══════════════════════════════════════════════════════════
echo.
pause
