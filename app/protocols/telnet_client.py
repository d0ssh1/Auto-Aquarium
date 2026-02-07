"""
Telnet Client для Optoma проекторов.

Модуль для управления Optoma проекторами через Telnet с RS232-over-TCP протоколом.
Включает retry logic с exponential backoff и структурированное логирование.

Использование:
    client = TelnetClient(timeout=5, max_retries=3)
    result = await client.power_on("192.168.2.64")
"""

import asyncio
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Callable, Any
import json

import structlog

logger = structlog.get_logger()


class CommandType(Enum):
    """Типы команд для Optoma."""
    POWER_ON = "power_on"
    POWER_OFF = "power_off"
    STATUS = "status"
    BLANK_ON = "blank_on"
    BLANK_OFF = "blank_off"


@dataclass
class TelnetResult:
    """Результат выполнения telnet команды."""
    success: bool
    message: str
    command_type: CommandType
    device_ip: str
    device_port: int
    attempt_count: int
    total_duration_ms: int
    response: Optional[str] = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    timestamps: list = field(default_factory=list)
    
    def to_dict(self) -> dict:
        """Конвертировать в словарь для логирования."""
        return {
            "success": self.success,
            "message": self.message,
            "command_type": self.command_type.value,
            "device_ip": self.device_ip,
            "device_port": self.device_port,
            "attempt_count": self.attempt_count,
            "total_duration_ms": self.total_duration_ms,
            "response": self.response,
            "error": self.error,
            "error_type": self.error_type,
            "timestamps": self.timestamps
        }
    
    def to_json(self) -> str:
        """Конвертировать в JSON строку."""
        return json.dumps(self.to_dict(), ensure_ascii=False)


class OptomaCommands:
    """
    RS232 команды для проекторов Optoma.
    
    Формат команды: ~AAAA N
    - AAAA: ID проектора (0000 для broadcast)
    - N: код команды
    """
    POWER_ON = "~0000 1\r"
    POWER_OFF = "~0000 0\r"
    STATUS_QUERY = "~00124 1\r"
    MUTE_ON = "~0000 2\r"
    MUTE_OFF = "~0000 3\r"
    BLANK_ON = "~00200 1\r"
    BLANK_OFF = "~00200 0\r"
    
    @classmethod
    def get_command(cls, cmd_type: CommandType) -> str:
        """Получить команду по типу."""
        mapping = {
            CommandType.POWER_ON: cls.POWER_ON,
            CommandType.POWER_OFF: cls.POWER_OFF,
            CommandType.STATUS: cls.STATUS_QUERY,
            CommandType.BLANK_ON: cls.BLANK_ON,
            CommandType.BLANK_OFF: cls.BLANK_OFF,
        }
        return mapping.get(cmd_type, cls.STATUS_QUERY)


class TelnetClient:
    """
    Telnet клиент для управления Optoma проекторами.
    
    Особенности:
    - Асинхронное подключение с настраиваемым таймаутом
    - Retry logic с exponential backoff
    - Детальное структурированное логирование
    - Dependency injection для тестирования
    
    Attributes:
        timeout: Таймаут на одну операцию (секунды)
        max_retries: Максимальное количество попыток
        base_delay: Базовая задержка между попытками (секунды)
        max_delay: Максимальная задержка между попытками (секунды)
    """
    
    DEFAULT_PORT = 23
    
    def __init__(
        self,
        timeout: int = 5,
        max_retries: int = 3,
        base_delay: int = 30,
        max_delay: int = 120,
        socket_factory: Optional[Callable] = None
    ):
        """
        Инициализация клиента.
        
        Args:
            timeout: Таймаут подключения и операций (секунды)
            max_retries: Количество повторных попыток
            base_delay: Базовая задержка для exponential backoff
            max_delay: Максимальная задержка между попытками
            socket_factory: Фабрика сокетов (для тестирования)
        """
        self.timeout = timeout
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self._socket_factory = socket_factory or self._create_socket
    
    def _create_socket(self) -> socket.socket:
        """Создать TCP сокет с настройками по умолчанию."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        return sock
    
    def _calculate_delay(self, attempt: int) -> float:
        """
        Рассчитать задержку с exponential backoff.
        
        Formula: min(base_delay * 2^attempt, max_delay)
        
        Args:
            attempt: Номер попытки (0-based)
            
        Returns:
            Задержка в секундах
        """
        delay = self.base_delay * (2 ** attempt)
        return min(delay, self.max_delay)
    
    def _send_sync(
        self,
        ip: str,
        port: int,
        command: str
    ) -> tuple[bool, str, Optional[str]]:
        """
        Синхронная отправка команды (для использования в executor).
        
        Args:
            ip: IP адрес устройства
            port: Порт устройства
            command: Команда для отправки
            
        Returns:
            Кортеж (success, response_or_message, error_type)
        """
        sock = None
        try:
            sock = self._socket_factory()
            sock.settimeout(self.timeout)
            
            # Подключение
            sock.connect((ip, port))
            
            # Отправка команды
            sock.sendall(command.encode('ascii'))
            
            # Небольшая пауза для обработки проектором
            time.sleep(0.3)
            
            # Чтение ответа
            sock.settimeout(2)  # Короткий таймаут для чтения
            try:
                response = sock.recv(1024).decode('ascii', errors='ignore').strip()
            except socket.timeout:
                response = ""  # Некоторые команды не возвращают ответ
            
            return (True, response, None)
            
        except socket.timeout:
            return (False, "Connection timeout", "TIMEOUT")
            
        except ConnectionRefusedError:
            return (False, "Connection refused by device", "CONNECTION_REFUSED")
            
        except OSError as e:
            if e.errno == 10061:  # Windows: connection refused
                return (False, "Connection refused by device", "CONNECTION_REFUSED")
            elif e.errno == 10060:  # Windows: timeout
                return (False, "Connection timeout", "TIMEOUT")
            elif e.errno == 10065:  # Windows: no route to host
                return (False, "No route to host", "NETWORK_UNREACHABLE")
            else:
                return (False, f"OS error: {e}", "OS_ERROR")
                
        except Exception as e:
            return (False, f"Unexpected error: {e}", "UNKNOWN_ERROR")
            
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
    
    async def send_command(
        self,
        ip: str,
        command: str,
        port: int = None,
        cmd_type: CommandType = CommandType.STATUS
    ) -> TelnetResult:
        """
        Отправить команду через telnet с retry logic.
        
        Args:
            ip: IP адрес устройства
            command: Команда для отправки
            port: Порт устройства (по умолчанию 23)
            cmd_type: Тип команды для логирования
            
        Returns:
            TelnetResult с результатом операции
        """
        if port is None:
            port = self.DEFAULT_PORT
        
        start_time = time.time()
        timestamps = []
        last_error = None
        last_error_type = None
        last_response = None
        
        for attempt in range(self.max_retries):
            attempt_start = time.time()
            attempt_timestamp = datetime.now().isoformat()
            
            logger.info(
                "telnet_attempt_start",
                device_ip=ip,
                device_port=port,
                command_type=cmd_type.value,
                attempt=attempt + 1,
                max_attempts=self.max_retries
            )
            
            # Выполняем в executor чтобы не блокировать event loop
            loop = asyncio.get_event_loop()
            success, response, error_type = await loop.run_in_executor(
                None,
                self._send_sync,
                ip,
                port,
                command
            )
            
            attempt_duration = int((time.time() - attempt_start) * 1000)
            
            timestamps.append({
                "attempt": attempt + 1,
                "timestamp": attempt_timestamp,
                "duration_ms": attempt_duration,
                "success": success,
                "response": response if success else None,
                "error": response if not success else None
            })
            
            if success:
                logger.info(
                    "telnet_command_success",
                    device_ip=ip,
                    device_port=port,
                    command_type=cmd_type.value,
                    attempt=attempt + 1,
                    duration_ms=attempt_duration,
                    response=response[:100] if response else None
                )
                
                total_duration = int((time.time() - start_time) * 1000)
                return TelnetResult(
                    success=True,
                    message="Command executed successfully",
                    command_type=cmd_type,
                    device_ip=ip,
                    device_port=port,
                    attempt_count=attempt + 1,
                    total_duration_ms=total_duration,
                    response=response,
                    timestamps=timestamps
                )
            
            # Сохраняем последнюю ошибку
            last_error = response
            last_error_type = error_type
            
            logger.warning(
                "telnet_attempt_failed",
                device_ip=ip,
                device_port=port,
                command_type=cmd_type.value,
                attempt=attempt + 1,
                error=response,
                error_type=error_type,
                duration_ms=attempt_duration
            )
            
            # Exponential backoff перед следующей попыткой
            if attempt < self.max_retries - 1:
                delay = self._calculate_delay(attempt)
                logger.info(
                    "telnet_retry_waiting",
                    device_ip=ip,
                    next_attempt=attempt + 2,
                    delay_seconds=delay
                )
                await asyncio.sleep(delay)
        
        # Все попытки исчерпаны
        total_duration = int((time.time() - start_time) * 1000)
        
        logger.error(
            "telnet_command_failed",
            device_ip=ip,
            device_port=port,
            command_type=cmd_type.value,
            total_attempts=self.max_retries,
            total_duration_ms=total_duration,
            last_error=last_error,
            error_type=last_error_type
        )
        
        return TelnetResult(
            success=False,
            message=f"All {self.max_retries} attempts failed",
            command_type=cmd_type,
            device_ip=ip,
            device_port=port,
            attempt_count=self.max_retries,
            total_duration_ms=total_duration,
            error=last_error,
            error_type=last_error_type,
            timestamps=timestamps
        )
    
    async def power_on(self, ip: str, port: int = None) -> TelnetResult:
        """
        Включить Optoma проектор.
        
        Args:
            ip: IP адрес проектора
            port: Порт (по умолчанию 23)
            
        Returns:
            TelnetResult
        """
        return await self.send_command(
            ip=ip,
            command=OptomaCommands.POWER_ON,
            port=port,
            cmd_type=CommandType.POWER_ON
        )
    
    async def power_off(self, ip: str, port: int = None) -> TelnetResult:
        """
        Выключить Optoma проектор.
        
        Args:
            ip: IP адрес проектора
            port: Порт (по умолчанию 23)
            
        Returns:
            TelnetResult
        """
        return await self.send_command(
            ip=ip,
            command=OptomaCommands.POWER_OFF,
            port=port,
            cmd_type=CommandType.POWER_OFF
        )
    
    async def get_status(self, ip: str, port: int = None) -> TelnetResult:
        """
        Получить статус Optoma проектора.
        
        Args:
            ip: IP адрес проектора
            port: Порт (по умолчанию 23)
            
        Returns:
            TelnetResult
        """
        return await self.send_command(
            ip=ip,
            command=OptomaCommands.STATUS_QUERY,
            port=port,
            cmd_type=CommandType.STATUS
        )
    
    async def check_reachable(self, ip: str, port: int = None) -> bool:
        """
        Проверить доступность устройства.
        
        Args:
            ip: IP адрес
            port: Порт
            
        Returns:
            True если устройство доступно
        """
        if port is None:
            port = self.DEFAULT_PORT
        
        try:
            loop = asyncio.get_event_loop()
            
            def _probe():
                sock = self._socket_factory()
                sock.settimeout(2)
                try:
                    sock.connect((ip, port))
                    sock.close()
                    return True
                except Exception:
                    return False
                finally:
                    try:
                        sock.close()
                    except Exception:
                        pass
            
            return await loop.run_in_executor(None, _probe)
        except Exception:
            return False


# Пример использования:
if __name__ == "__main__":
    import asyncio
    
    async def main():
        # Создаём клиент с настройками
        client = TelnetClient(
            timeout=5,
            max_retries=3,
            base_delay=30,
            max_delay=120
        )
        
        # Проверяем доступность
        ip = "192.168.2.64"
        print(f"\nПроверка доступности {ip}...")
        is_reachable = await client.check_reachable(ip)
        print(f"Устройство доступно: {is_reachable}")
        
        if is_reachable:
            # Включаем проектор
            print(f"\nВключение проектора {ip}...")
            result = await client.power_on(ip)
            
            print("\nРезультат:")
            print(f"  Успех: {result.success}")
            print(f"  Попыток: {result.attempt_count}")
            print(f"  Время: {result.total_duration_ms}ms")
            if result.error:
                print(f"  Ошибка: {result.error}")
            
            # JSON лог
            print("\nJSON лог:")
            print(result.to_json())
    
    asyncio.run(main())
