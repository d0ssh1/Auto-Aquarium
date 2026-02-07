"""
Device Monitor — комплексная проверка статуса устройств.

Модуль для проверки доступности и состояния устройств через:
- ICMP Ping
- TCP Port probe
- HTTP GET (если применимо)
- Zabbix API (если настроен)

Использование:
    monitor = DeviceMonitor()
    status = await monitor.check_device("192.168.2.64", port=23)
"""

import asyncio
import subprocess
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any

import structlog
import httpx

logger = structlog.get_logger()


class CheckType(Enum):
    """Типы проверок."""
    PING = "ping"
    TCP = "tcp"
    HTTP = "http"
    ZABBIX = "zabbix"


class DeviceState(Enum):
    """Состояние устройства."""
    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"  # Частично работает
    UNKNOWN = "unknown"


@dataclass
class CheckResult:
    """Результат одной проверки."""
    check_type: CheckType
    success: bool
    duration_ms: int
    message: str
    extra_data: Optional[Dict] = None


@dataclass
class DeviceStatus:
    """Полный статус устройства."""
    ip: str
    port: Optional[int]
    state: DeviceState
    is_reachable: bool
    ping_ok: bool
    tcp_ok: bool
    http_ok: Optional[bool]
    zabbix_data: Optional[Dict]
    checks: List[CheckResult]
    total_duration_ms: int
    checked_at: str
    
    def to_dict(self) -> dict:
        """Конвертировать в словарь."""
        return {
            "ip": self.ip,
            "port": self.port,
            "state": self.state.value,
            "is_reachable": self.is_reachable,
            "ping_ok": self.ping_ok,
            "tcp_ok": self.tcp_ok,
            "http_ok": self.http_ok,
            "zabbix_data": self.zabbix_data,
            "checks": [
                {
                    "type": c.check_type.value,
                    "success": c.success,
                    "duration_ms": c.duration_ms,
                    "message": c.message
                }
                for c in self.checks
            ],
            "total_duration_ms": self.total_duration_ms,
            "checked_at": self.checked_at
        }


class DeviceMonitor:
    """
    Монитор состояния устройств.
    
    Выполняет многоуровневую проверку:
    1. ICMP Ping — базовая доступность
    2. TCP Port probe — сервис слушает порт
    3. HTTP GET — веб-интерфейс отвечает (опционально)
    4. Zabbix API — метрики из Zabbix (опционально)
    
    Attributes:
        ping_timeout: Таймаут ping в секундах
        tcp_timeout: Таймаут TCP probe в секундах
        http_timeout: Таймаут HTTP запроса в секундах
        zabbix_client: Клиент Zabbix API (опционально)
    """
    
    def __init__(
        self,
        ping_timeout: float = 2.0,
        tcp_timeout: float = 1.0,
        http_timeout: float = 1.0,
        zabbix_client: Optional[Any] = None
    ):
        """
        Инициализация монитора.
        
        Args:
            ping_timeout: Таймаут ping
            tcp_timeout: Таймаут TCP подключения
            http_timeout: Таймаут HTTP запроса
            zabbix_client: Клиент ZabbixAPI (опционально)
        """
        self.ping_timeout = ping_timeout
        self.tcp_timeout = tcp_timeout
        self.http_timeout = http_timeout
        self.zabbix_client = zabbix_client
    
    async def ping(self, ip: str) -> CheckResult:
        """
        Проверить доступность через ICMP ping.
        
        Windows: ping -n 1 -w <timeout_ms> <ip>
        
        Args:
            ip: IP адрес для проверки
            
        Returns:
            CheckResult
        """
        start_time = time.time()
        timeout_ms = int(self.ping_timeout * 1000)
        
        try:
            # Windows ping
            process = await asyncio.create_subprocess_exec(
                "ping", "-n", "1", "-w", str(timeout_ms), ip,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.ping_timeout + 1
                )
            except asyncio.TimeoutError:
                process.kill()
                duration_ms = int((time.time() - start_time) * 1000)
                return CheckResult(
                    check_type=CheckType.PING,
                    success=False,
                    duration_ms=duration_ms,
                    message="Ping timeout"
                )
            
            duration_ms = int((time.time() - start_time) * 1000)
            success = process.returncode == 0
            
            # Парсим время ответа из stdout
            rtt = None
            if success and stdout:
                output = stdout.decode('cp866', errors='ignore')
                # Ищем "time=XXms" или "время=XXмс"
                import re
                match = re.search(r'[=<](\d+)\s*m?[sс]', output, re.IGNORECASE)
                if match:
                    rtt = int(match.group(1))
            
            return CheckResult(
                check_type=CheckType.PING,
                success=success,
                duration_ms=duration_ms,
                message="Ping successful" if success else "Ping failed",
                extra_data={"rtt_ms": rtt} if rtt else None
            )
            
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return CheckResult(
                check_type=CheckType.PING,
                success=False,
                duration_ms=duration_ms,
                message=f"Ping error: {e}"
            )
    
    async def probe_tcp(self, ip: str, port: int) -> CheckResult:
        """
        Проверить доступность TCP порта.
        
        Args:
            ip: IP адрес
            port: TCP порт
            
        Returns:
            CheckResult
        """
        start_time = time.time()
        
        try:
            # Пытаемся подключиться
            future = asyncio.open_connection(ip, port)
            reader, writer = await asyncio.wait_for(
                future,
                timeout=self.tcp_timeout
            )
            
            # Успешно подключились
            writer.close()
            await writer.wait_closed()
            
            duration_ms = int((time.time() - start_time) * 1000)
            return CheckResult(
                check_type=CheckType.TCP,
                success=True,
                duration_ms=duration_ms,
                message=f"TCP port {port} is open"
            )
            
        except asyncio.TimeoutError:
            duration_ms = int((time.time() - start_time) * 1000)
            return CheckResult(
                check_type=CheckType.TCP,
                success=False,
                duration_ms=duration_ms,
                message=f"TCP port {port} timeout"
            )
            
        except ConnectionRefusedError:
            duration_ms = int((time.time() - start_time) * 1000)
            return CheckResult(
                check_type=CheckType.TCP,
                success=False,
                duration_ms=duration_ms,
                message=f"TCP port {port} refused"
            )
            
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return CheckResult(
                check_type=CheckType.TCP,
                success=False,
                duration_ms=duration_ms,
                message=f"TCP error: {e}"
            )
    
    async def probe_http(self, ip: str, port: int = 80) -> CheckResult:
        """
        Проверить доступность HTTP сервиса.
        
        Args:
            ip: IP адрес
            port: HTTP порт (по умолчанию 80)
            
        Returns:
            CheckResult
        """
        start_time = time.time()
        url = f"http://{ip}:{port}/"
        
        try:
            async with httpx.AsyncClient(timeout=self.http_timeout) as client:
                response = await client.get(url)
            
            duration_ms = int((time.time() - start_time) * 1000)
            success = response.status_code < 500
            
            return CheckResult(
                check_type=CheckType.HTTP,
                success=success,
                duration_ms=duration_ms,
                message=f"HTTP {response.status_code}",
                extra_data={"status_code": response.status_code}
            )
            
        except httpx.TimeoutException:
            duration_ms = int((time.time() - start_time) * 1000)
            return CheckResult(
                check_type=CheckType.HTTP,
                success=False,
                duration_ms=duration_ms,
                message="HTTP timeout"
            )
            
        except httpx.ConnectError:
            duration_ms = int((time.time() - start_time) * 1000)
            return CheckResult(
                check_type=CheckType.HTTP,
                success=False,
                duration_ms=duration_ms,
                message="HTTP connection failed"
            )
            
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return CheckResult(
                check_type=CheckType.HTTP,
                success=False,
                duration_ms=duration_ms,
                message=f"HTTP error: {e}"
            )
    
    async def check_zabbix(self, host_name: str) -> CheckResult:
        """
        Получить статус из Zabbix API.
        
        Args:
            host_name: Имя хоста в Zabbix
            
        Returns:
            CheckResult с данными из Zabbix
        """
        start_time = time.time()
        
        if not self.zabbix_client:
            return CheckResult(
                check_type=CheckType.ZABBIX,
                success=False,
                duration_ms=0,
                message="Zabbix client not configured"
            )
        
        try:
            # Запрашиваем данные из Zabbix
            result = await self.zabbix_client.get_host_status(host_name)
            
            duration_ms = int((time.time() - start_time) * 1000)
            
            if result:
                return CheckResult(
                    check_type=CheckType.ZABBIX,
                    success=True,
                    duration_ms=duration_ms,
                    message="Zabbix data retrieved",
                    extra_data=result
                )
            else:
                return CheckResult(
                    check_type=CheckType.ZABBIX,
                    success=False,
                    duration_ms=duration_ms,
                    message="Host not found in Zabbix"
                )
                
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return CheckResult(
                check_type=CheckType.ZABBIX,
                success=False,
                duration_ms=duration_ms,
                message=f"Zabbix error: {e}"
            )
    
    async def check_device(
        self,
        ip: str,
        port: Optional[int] = None,
        check_http: bool = False,
        http_port: int = 80,
        zabbix_host: Optional[str] = None
    ) -> DeviceStatus:
        """
        Выполнить полную проверку устройства.
        
        Логика:
        1. Ping — если не отвечает, устройство offline
        2. TCP port — если указан порт и ping OK
        3. HTTP — если check_http=True
        4. Zabbix — если zabbix_host указан
        
        Args:
            ip: IP адрес устройства
            port: Порт для TCP проверки (опционально)
            check_http: Проверять HTTP доступность
            http_port: Порт для HTTP
            zabbix_host: Имя хоста в Zabbix
            
        Returns:
            DeviceStatus с полной информацией
        """
        start_time = time.time()
        checks: List[CheckResult] = []
        
        logger.info(
            "device_check_start",
            ip=ip,
            port=port,
            check_http=check_http,
            zabbix_host=zabbix_host
        )
        
        # 1. Ping
        ping_result = await self.ping(ip)
        checks.append(ping_result)
        
        # Если ping не прошёл — устройство offline
        if not ping_result.success:
            logger.warning(
                "device_offline",
                ip=ip,
                reason="ping_failed"
            )
            
            total_duration = int((time.time() - start_time) * 1000)
            return DeviceStatus(
                ip=ip,
                port=port,
                state=DeviceState.OFFLINE,
                is_reachable=False,
                ping_ok=False,
                tcp_ok=False,
                http_ok=None,
                zabbix_data=None,
                checks=checks,
                total_duration_ms=total_duration,
                checked_at=datetime.now().isoformat()
            )
        
        # 2. TCP port probe
        tcp_ok = False
        if port:
            tcp_result = await self.probe_tcp(ip, port)
            checks.append(tcp_result)
            tcp_ok = tcp_result.success
        
        # 3. HTTP check
        http_ok = None
        if check_http:
            http_result = await self.probe_http(ip, http_port)
            checks.append(http_result)
            http_ok = http_result.success
        
        # 4. Zabbix data
        zabbix_data = None
        if zabbix_host:
            zabbix_result = await self.check_zabbix(zabbix_host)
            checks.append(zabbix_result)
            if zabbix_result.success:
                zabbix_data = zabbix_result.extra_data
        
        # Определяем итоговое состояние
        if tcp_ok or (port is None and ping_result.success):
            state = DeviceState.ONLINE
        elif ping_result.success and port and not tcp_ok:
            state = DeviceState.DEGRADED
        else:
            state = DeviceState.UNKNOWN
        
        total_duration = int((time.time() - start_time) * 1000)
        
        logger.info(
            "device_check_complete",
            ip=ip,
            state=state.value,
            ping_ok=ping_result.success,
            tcp_ok=tcp_ok,
            total_duration_ms=total_duration
        )
        
        return DeviceStatus(
            ip=ip,
            port=port,
            state=state,
            is_reachable=ping_result.success,
            ping_ok=ping_result.success,
            tcp_ok=tcp_ok,
            http_ok=http_ok,
            zabbix_data=zabbix_data,
            checks=checks,
            total_duration_ms=total_duration,
            checked_at=datetime.now().isoformat()
        )
    
    async def check_multiple(
        self,
        devices: List[Dict[str, Any]],
        parallel: bool = True
    ) -> List[DeviceStatus]:
        """
        Проверить несколько устройств.
        
        Args:
            devices: Список устройств [{"ip": "...", "port": ..., ...}]
            parallel: Выполнять параллельно
            
        Returns:
            Список DeviceStatus
        """
        if parallel:
            tasks = [
                self.check_device(
                    ip=d["ip"],
                    port=d.get("port"),
                    check_http=d.get("check_http", False),
                    http_port=d.get("http_port", 80),
                    zabbix_host=d.get("zabbix_host")
                )
                for d in devices
            ]
            return await asyncio.gather(*tasks)
        else:
            results = []
            for d in devices:
                result = await self.check_device(
                    ip=d["ip"],
                    port=d.get("port"),
                    check_http=d.get("check_http", False),
                    http_port=d.get("http_port", 80),
                    zabbix_host=d.get("zabbix_host")
                )
                results.append(result)
            return results


# Global instance
device_monitor = DeviceMonitor()


# Пример использования:
if __name__ == "__main__":
    import asyncio
    import json
    
    async def main():
        monitor = DeviceMonitor(
            ping_timeout=2.0,
            tcp_timeout=1.0,
            http_timeout=1.0
        )
        
        # Проверка одного устройства
        print("Проверка устройства 192.168.2.64:23...")
        status = await monitor.check_device(
            ip="192.168.2.64",
            port=23
        )
        
        print("\nРезультат:")
        print(f"  IP: {status.ip}")
        print(f"  Состояние: {status.state.value}")
        print(f"  Ping: {'OK' if status.ping_ok else 'FAIL'}")
        print(f"  TCP: {'OK' if status.tcp_ok else 'FAIL'}")
        print(f"  Время: {status.total_duration_ms}ms")
        
        print("\nДетали проверок:")
        for check in status.checks:
            print(f"  {check.check_type.value}: {check.message} ({check.duration_ms}ms)")
        
        # Проверка нескольких устройств параллельно
        print("\n\nПроверка нескольких устройств...")
        devices = [
            {"ip": "192.168.2.64", "port": 23},
            {"ip": "192.168.2.95", "port": 9090},
            {"ip": "192.168.8.25", "port": 7992},
        ]
        
        results = await monitor.check_multiple(devices, parallel=True)
        
        print("\nСводка:")
        for status in results:
            emoji = "✅" if status.state == DeviceState.ONLINE else "❌"
            print(f"  {emoji} {status.ip}:{status.port} — {status.state.value}")
    
    asyncio.run(main())
