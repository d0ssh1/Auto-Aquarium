@echo off
chcp 65001 >nul
title Ocean Aquarium Control System

echo.
echo ╔══════════════════════════════════════════════════════════╗
echo ║     🌊 OCEAN AQUARIUM EQUIPMENT CONTROL SYSTEM 🌊        ║
echo ╠══════════════════════════════════════════════════════════╣
echo ║  Starting server...                                       ║
echo ║  URL: http://localhost:8000                              ║
echo ║  Press Ctrl+C to stop                                    ║
echo ╚══════════════════════════════════════════════════════════╝
echo.

REM Go to project root (parent of scripts folder)
cd /d "%~dp0.."

REM Open browser after 2 seconds delay (gives server time to start)
start "" cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:8000"

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
