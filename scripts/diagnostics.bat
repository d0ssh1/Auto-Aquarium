@echo off
chcp 65001 >nul
title System Diagnostics - Ocean Aquarium Control System

echo.
echo ╔══════════════════════════════════════════════════════════╗
echo ║     🌊 OCEAN AQUARIUM SYSTEM DIAGNOSTICS 🌊              ║
echo ║                                                           ║
echo ║  Проверка системы БЕЗ включения/выключения устройств     ║
echo ║                                                           ║
echo ║  Проверяется:                                             ║
echo ║  • Конфигурация (config.json)                            ║
echo ║  • Импорт всех модулей Python                            ║
echo ║  • Сетевая доступность устройств (ping)                  ║
echo ║  • TCP порты устройств (Telnet/Barco)                    ║
echo ║  • Zabbix API                                            ║
echo ║  • База данных планировщика                              ║
echo ╚══════════════════════════════════════════════════════════╝
echo.

REM Go to project root (parent of scripts folder)
cd /d "%~dp0.."

REM Activate venv if exists
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

echo Запуск диагностики...
echo.

python scripts\diagnostics.py

echo.
echo ══════════════════════════════════════════════════════════
echo Диагностика завершена.
echo Логи сохранены в папке: logs\
echo ══════════════════════════════════════════════════════════
echo.
pause
