@echo off
chcp 65001 >nul
title Stop Server - Ocean Aquarium Control System

echo.
echo ══════════════════════════════════════════════════════════
echo   Остановка сервера Ocean Aquarium...
echo ══════════════════════════════════════════════════════════
echo.

REM Find and kill Python processes running main.py on port 8000
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000"') do (
    echo Stopping process PID: %%a
    taskkill /PID %%a /F >nul 2>&1
)

echo.
echo ✅ Сервер остановлен.
echo.
pause
