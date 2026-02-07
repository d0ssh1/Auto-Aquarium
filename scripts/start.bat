@echo off
chcp 65001 >nul
title Ocean Aquarium Control System

echo.
echo ╔══════════════════════════════════════════════════════════╗
echo ║     🌊 OCEAN AQUARIUM EQUIPMENT CONTROL SYSTEM 🌊        ║
echo ╠══════════════════════════════════════════════════════════╣
echo ║  Starting server...                                       ║
echo ╚══════════════════════════════════════════════════════════╝
echo.

REM Go to project root (parent of scripts folder)
cd /d "%~dp0.."

REM Check if venv exists
if exist "venv\Scripts\python.exe" (
    echo Using virtual environment...
    call venv\Scripts\activate.bat
    python main.py
) else (
    echo Using system Python...
    python main.py
)

echo.
echo Server stopped.
pause
