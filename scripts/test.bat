@echo off
chcp 65001 >nul
title Running Tests - Ocean Aquarium Control System

echo.
echo ╔══════════════════════════════════════════════════════════╗
echo ║     🌊 OCEAN AQUARIUM EQUIPMENT CONTROL SYSTEM 🌊        ║
echo ║                    RUNNING TESTS                          ║
echo ╚══════════════════════════════════════════════════════════╝
echo.

REM Go to project root (parent of scripts folder)
cd /d "%~dp0.."

REM Activate venv if exists
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

echo Running pytest...
echo.

python -m pytest tests/ -v --tb=short

echo.
echo ══════════════════════════════════════════════════════════
echo Tests completed.
echo ══════════════════════════════════════════════════════════
echo.
pause
