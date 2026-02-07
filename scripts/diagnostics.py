"""
Ocean Aquarium System Diagnostics
=================================

Этот скрипт проверяет работоспособность ВСЕХ компонентов системы
БЕЗ реального включения/выключения оборудования.

Проверяется:
1. Конфигурация (config.json)
2. Сетевая доступность устройств (ping)
3. TCP-порты устройств (Telnet: 23, Barco: 9090)
4. Zabbix API (если настроен)
5. Импорт всех модулей
6. База данных планировщика

Результаты сохраняются в: logs/diagnostics_YYYYMMDD_HHMMSS.log

Использование:
    python scripts/diagnostics.py
"""

import asyncio
import io
import json
import socket
import sys
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Добавляем корень проекта
ROOT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT_DIR))

# Файл лога
LOGS_DIR = ROOT_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)
LOG_FILE = LOGS_DIR / f"diagnostics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"


class DiagnosticsLogger:
    """Логгер для диагностики."""
    
    def __init__(self, log_file: Path):
        self.log_file = log_file
        self.lines: List[str] = []
        self.errors: List[str] = []
        self.warnings: List[str] = []
        
    def log(self, message: str, level: str = "INFO"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] [{level}] {message}"
        self.lines.append(line)
        print(line)
        
        if level == "ERROR":
            self.errors.append(message)
        elif level == "WARNING":
            self.warnings.append(message)
    
    def info(self, msg: str):
        self.log(msg, "INFO")
    
    def ok(self, msg: str):
        self.log(f"✅ {msg}", "OK")
    
    def error(self, msg: str):
        self.log(f"❌ {msg}", "ERROR")
    
    def warning(self, msg: str):
        self.log(f"⚠️ {msg}", "WARNING")
    
    def section(self, title: str):
        sep = "=" * 60
        self.log("")
        self.log(sep)
        self.log(f"  {title}")
        self.log(sep)
    
    def save(self):
        with open(self.log_file, "w", encoding="utf-8") as f:
            f.write("\n".join(self.lines))
        return self.log_file


class SystemDiagnostics:
    """Полная диагностика системы."""
    
    def __init__(self):
        self.log = DiagnosticsLogger(LOG_FILE)
        self.config: Dict[str, Any] = {}
        self.results: Dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "modules": {},
            "devices": {},
            "network": {},
            "zabbix": {},
            "database": {}
        }
    
    async def run_all(self):
        """Запустить все проверки."""
        self.log.section("OCEAN AQUARIUM SYSTEM DIAGNOSTICS")
        self.log.info(f"Время запуска: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self.log.info(f"Прект: {ROOT_DIR}")
        self.log.info(f"Лог-файл: {LOG_FILE}")
        
        # 1. Проверка конфигурации
        await self.check_config()
        
        # 2. Проверка модулей
        await self.check_modules()
        
        # 3. Проверка сетевой доступности
        await self.check_network()
        
        # 4. Проверка TCP портов
        await self.check_tcp_ports()
        
        # 5. Проверка Zabbix
        await self.check_zabbix()
        
        # 6. Проверка базы данных
        await self.check_database()
        
        # Итоги
        self.print_summary()
        
        # Сохранение
        log_path = self.log.save()
        self.save_json_report()
        
        return self.results
    
    async def check_config(self):
        """Проверка конфигурации."""
        self.log.section("1. ПРОВЕРКА КОНФИГУРАЦИИ")
        
        config_path = ROOT_DIR / "config.json"
        
        if not config_path.exists():
            self.log.error(f"config.json не найден: {config_path}")
            return
        
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                self.config = json.load(f)
            self.log.ok(f"config.json загружен успешно")
        except json.JSONDecodeError as e:
            self.log.error(f"Ошибка парсинга JSON: {e}")
            return
        except Exception as e:
            self.log.error(f"Ошибка чтения файла: {e}")
            return
        
        # Проверяем структуру
        devices = self.config.get("devices", [])
        self.log.info(f"Устройств в конфиге: {len(devices)}")
        
        # Группируем по типам
        device_types: Dict[str, int] = {}
        for d in devices:
            dtype = d.get("device_type", "unknown")
            device_types[dtype] = device_types.get(dtype, 0) + 1
        
        for dtype, count in device_types.items():
            self.log.info(f"  - {dtype}: {count}")
        
        # Проверяем расписание
        schedule = self.config.get("schedule", {})
        if schedule:
            self.log.ok(f"Расписание: ON={schedule.get('on_time')}, OFF={schedule.get('off_time')}")
        else:
            self.log.warning("Расписание не настроено")
        
        # Проверяем Zabbix
        zabbix = self.config.get("zabbix", {})
        if zabbix.get("enabled"):
            self.log.info(f"Zabbix API: {zabbix.get('url')}")
        else:
            self.log.warning("Zabbix не настроен (monitoring.zabbix_enabled = false)")
        
        self.results["config"] = {
            "loaded": True,
            "devices_count": len(devices),
            "device_types": device_types,
            "schedule": schedule,
            "zabbix_enabled": zabbix.get("enabled", False)
        }
    
    async def check_modules(self):
        """Проверка импорта модулей."""
        self.log.section("2. ПРОВЕРКА МОДУЛЕЙ")
        
        modules_to_check = [
            ("app.core.device_registry", "DeviceRegistry, Device"),
            ("app.core.logger_service", "LoggerService, get_logger_service"),
            ("app.protocols.telnet_client", "TelnetClient, CommandType"),
            ("app.protocols.barco_client", "BarcoClient, BarcoCommand"),
            ("app.protocols.device_monitor", "DeviceMonitor, DeviceStatus"),
            ("app.services.scheduler_service", "SchedulerService"),
            ("app.services.device_manager", "DeviceManager"),
            ("app.services.monitor_service", "MonitorService"),
            ("app.services.reports", "ReportGenerator"),
        ]
        
        for module_path, components in modules_to_check:
            try:
                module = __import__(module_path, fromlist=components.split(", "))
                self.log.ok(f"{module_path}")
                self.results["modules"][module_path] = {"status": "ok"}
            except ImportError as e:
                self.log.error(f"{module_path}: {e}")
                self.results["modules"][module_path] = {"status": "error", "error": str(e)}
            except Exception as e:
                self.log.error(f"{module_path}: {e}")
                self.results["modules"][module_path] = {"status": "error", "error": str(e)}
        
        # Проверяем внешние зависимости
        self.log.info("")
        self.log.info("Внешние зависимости:")
        
        external_deps = [
            "fastapi",
            "uvicorn",
            "structlog",
            "pydantic",
            "sqlalchemy",
            "apscheduler",
            "httpx",
        ]
        
        for dep in external_deps:
            try:
                module = __import__(dep)
                version = getattr(module, "__version__", "?")
                self.log.ok(f"{dep} v{version}")
            except ImportError:
                self.log.error(f"{dep} - НЕ УСТАНОВЛЕН")
    
    async def check_network(self):
        """Проверка сетевой доступности устройств (ping)."""
        self.log.section("3. ПРОВЕРКА СЕТЕВОЙ ДОСТУПНОСТИ (PING)")
        
        devices = self.config.get("devices", [])
        if not devices:
            self.log.warning("Нет устройств для проверки")
            return
        
        # Собираем уникальные IP
        ips = list(set(d.get("ip") for d in devices if d.get("ip")))
        self.log.info(f"Уникальных IP адресов: {len(ips)}")
        
        reachable = 0
        unreachable = 0
        unreachable_ips: List[str] = []
        
        for ip in ips:
            is_up = await self._ping(ip)
            if is_up:
                reachable += 1
                self.results["network"][ip] = {"ping": True}
            else:
                unreachable += 1
                unreachable_ips.append(ip)
                self.results["network"][ip] = {"ping": False}
        
        self.log.info("")
        self.log.info(f"Доступно: {reachable}/{len(ips)}")
        
        if unreachable > 0:
            self.log.warning(f"Недоступно: {unreachable}")
            for ip in unreachable_ips:
                # Найдём имена устройств с этим IP
                names = [d.get("name") for d in devices if d.get("ip") == ip]
                self.log.error(f"  {ip} - {', '.join(names)}")
    
    async def _ping(self, ip: str, timeout: int = 2) -> bool:
        """Пинг IP адреса."""
        try:
            # Windows ping
            result = subprocess.run(
                ["ping", "-n", "1", "-w", str(timeout * 1000), ip],
                capture_output=True,
                timeout=timeout + 1
            )
            return result.returncode == 0
        except Exception:
            return False
    
    async def check_tcp_ports(self):
        """Проверка TCP портов устройств."""
        self.log.section("4. ПРОВЕРКА TCP ПОРТОВ")
        
        devices = self.config.get("devices", [])
        if not devices:
            return
        
        # Группируем по типу
        telnet_devices = [d for d in devices if d.get("device_type") == "optoma_telnet"]
        barco_devices = [d for d in devices if d.get("device_type") == "barco_jsonrpc"]
        
        self.log.info(f"Optoma Telnet (порт 23): {len(telnet_devices)} устройств")
        self.log.info(f"Barco JSON-RPC (порт 9090): {len(barco_devices)} устройств")
        self.log.info("")
        
        # Проверяем Telnet
        if telnet_devices:
            self.log.info("--- Optoma Telnet ---")
            for dev in telnet_devices[:10]:  # Первые 10 для скорости
                ip = dev.get("ip")
                port = dev.get("port", 23)
                is_open = await self._check_tcp_port(ip, port)
                
                status = "✅ OPEN" if is_open else "❌ CLOSED"
                self.log.info(f"  {dev.get('name', ip)}: {ip}:{port} - {status}")
                
                self.results["devices"][dev.get("id")] = {
                    "name": dev.get("name"),
                    "ip": ip,
                    "port": port,
                    "tcp_open": is_open
                }
            
            if len(telnet_devices) > 10:
                self.log.info(f"  ... и ещё {len(telnet_devices) - 10} устройств")
        
        # Проверяем Barco
        if barco_devices:
            self.log.info("")
            self.log.info("--- Barco JSON-RPC ---")
            for dev in barco_devices:
                ip = dev.get("ip")
                port = dev.get("port", 9090)
                is_open = await self._check_tcp_port(ip, port)
                
                status = "✅ OPEN" if is_open else "❌ CLOSED"
                self.log.info(f"  {dev.get('name', ip)}: {ip}:{port} - {status}")
                
                self.results["devices"][dev.get("id")] = {
                    "name": dev.get("name"),
                    "ip": ip,
                    "port": port,
                    "tcp_open": is_open
                }
    
    async def _check_tcp_port(self, ip: str, port: int, timeout: float = 2) -> bool:
        """Проверить TCP порт."""
        try:
            loop = asyncio.get_event_loop()
            
            def _check():
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(timeout)
                try:
                    result = sock.connect_ex((ip, port))
                    return result == 0
                finally:
                    sock.close()
            
            return await loop.run_in_executor(None, _check)
        except Exception:
            return False
    
    async def check_zabbix(self):
        """Проверка Zabbix API."""
        self.log.section("5. ПРОВЕРКА ZABBIX API")
        
        zabbix_config = self.config.get("zabbix", {})
        
        if not zabbix_config.get("enabled"):
            self.log.warning("Zabbix отключён в конфигурации")
            self.results["zabbix"] = {"enabled": False}
            return
        
        url = zabbix_config.get("url")
        token = zabbix_config.get("api_token")
        
        if not url:
            self.log.error("Zabbix URL не указан")
            self.results["zabbix"] = {"enabled": True, "status": "no_url"}
            return
        
        self.log.info(f"URL: {url}")
        
        # Проверяем доступность URL
        try:
            import httpx
            
            async with httpx.AsyncClient(timeout=10) as client:
                # Простой запрос к API
                api_url = f"{url.rstrip('/')}/api_jsonrpc.php"
                
                # Проверяем apiinfo.version (не требует авторизации)
                payload = {
                    "jsonrpc": "2.0",
                    "method": "apiinfo.version",
                    "id": 1,
                    "params": {}
                }
                
                response = await client.post(api_url, json=payload)
                
                if response.status_code == 200:
                    data = response.json()
                    version = data.get("result")
                    if version:
                        self.log.ok(f"Zabbix API версия: {version}")
                        self.results["zabbix"] = {
                            "enabled": True,
                            "status": "ok",
                            "version": version
                        }
                    else:
                        self.log.warning(f"Неожиданный ответ: {data}")
                else:
                    self.log.error(f"HTTP {response.status_code}")
                    self.results["zabbix"] = {"enabled": True, "status": "http_error"}
                    
        except httpx.ConnectError as e:
            self.log.error(f"Не удалось подключиться к Zabbix: {e}")
            self.results["zabbix"] = {"enabled": True, "status": "connection_error"}
        except Exception as e:
            self.log.error(f"Ошибка Zabbix: {e}")
            self.results["zabbix"] = {"enabled": True, "status": "error", "error": str(e)}
    
    async def check_database(self):
        """Проверка базы данных планировщика."""
        self.log.section("6. ПРОВЕРКА БАЗЫ ДАННЫХ")
        
        db_path = ROOT_DIR / "data" / "scheduler.db"
        
        if not db_path.exists():
            self.log.warning(f"БД не существует (будет создана при первом запуске): {db_path}")
            self.results["database"] = {"exists": False}
            return
        
        try:
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            
            # Проверяем таблицы
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]
            
            self.log.ok(f"БД доступна: {db_path.name}")
            self.log.info(f"Таблицы: {', '.join(tables) if tables else 'нет'}")
            
            # Проверяем jobs если есть
            if "apscheduler_jobs" in tables:
                cursor.execute("SELECT COUNT(*) FROM apscheduler_jobs")
                job_count = cursor.fetchone()[0]
                self.log.info(f"Запланированных задач: {job_count}")
            
            conn.close()
            
            self.results["database"] = {
                "exists": True,
                "tables": tables
            }
            
        except Exception as e:
            self.log.error(f"Ошибка БД: {e}")
            self.results["database"] = {"exists": True, "error": str(e)}
    
    def print_summary(self):
        """Вывод итогов."""
        self.log.section("ИТОГИ ДИАГНОСТИКИ")
        
        total_errors = len(self.log.errors)
        total_warnings = len(self.log.warnings)
        
        if total_errors == 0 and total_warnings == 0:
            self.log.ok("Все проверки пройдены успешно!")
        else:
            if total_errors > 0:
                self.log.error(f"Ошибок: {total_errors}")
                for err in self.log.errors:
                    self.log.info(f"  • {err}")
            
            if total_warnings > 0:
                self.log.warning(f"Предупреждений: {total_warnings}")
        
        self.log.info("")
        self.log.info(f"Полный лог сохранён: {LOG_FILE}")
    
    def save_json_report(self):
        """Сохранить JSON отчёт."""
        json_path = LOG_FILE.with_suffix(".json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(self.results, f, indent=2, ensure_ascii=False, default=str)
        self.log.info(f"JSON отчёт: {json_path}")


async def main():
    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print("║     🌊 OCEAN AQUARIUM SYSTEM DIAGNOSTICS 🌊              ║")
    print("║                                                           ║")
    print("║  Проверка системы БЕЗ включения/выключения устройств     ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print()
    
    diagnostics = SystemDiagnostics()
    await diagnostics.run_all()
    
    print()
    print("Диагностика завершена.")
    print(f"Лог: {LOG_FILE}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
