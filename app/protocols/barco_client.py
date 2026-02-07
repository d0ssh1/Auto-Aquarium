"""
Barco Client для JSON-RPC управления проекторами.

Модуль для управления Barco проекторами через JSON-RPC 2.0 протокол.
Включает retry logic с exponential backoff и структурированное логирование.

Использование:
    client = BarcoClient(timeout=5, max_retries=3)
    result = await client.power_on("192.168.2.95")
"""

import asyncio
import json
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Callable, Any, Dict

import structlog

logger = structlog.get_logger()


class BarcoCommand(Enum):
    """Типы команд для Barco."""
    POWER_ON = "system.poweron"
    POWER_OFF = "system.poweroff"
    POWER_STATE_GET = "system.powerstate.get"
    LAMP_HOURS = "system.lamptime"
    INPUT_GET = "input.get"
    INPUT_SET = "input.set"


@dataclass
class BarcoResult:
    """Результат выполнения JSON-RPC команды."""
    success: bool
    message: str
    method: str
    device_ip: str
    device_port: int
    attempt_count: int
    total_duration_ms: int
    response_data: Optional[Dict] = None
    error: Optional[str] = None
    error_code: Optional[int] = None
    error_type: Optional[str] = None
    timestamps: list = field(default_factory=list)
    
    def to_dict(self) -> dict:
        """Конвертировать в словарь для логирования."""
        return {
            "success": self.success,
            "message": self.message,
            "method": self.method,
            "device_ip": self.device_ip,
            "device_port": self.device_port,
            "attempt_count": self.attempt_count,
            "total_duration_ms": self.total_duration_ms,
            "response_data": self.response_data,
            "error": self.error,
            "error_code": self.error_code,
            "error_type": self.error_type,
            "timestamps": self.timestamps
        }
    
    def to_json(self) -> str:
        """Конвертировать в JSON строку."""
        return json.dumps(self.to_dict(), ensure_ascii=False)


class BarcoClient:
    """
    JSON-RPC клиент для управления Barco проекторами.
    
    Особенности:
    - JSON-RPC 2.0 протокол через raw TCP socket
    - Асинхронное выполнение с настраиваемым таймаутом
    - Retry logic с exponential backoff
    - Детальное структурированное логирование
    - Dependency injection для тестирования
    
    Attributes:
        timeout: Таймаут на одну операцию (секунды)
        max_retries: Максимальное количество попыток
        base_delay: Базовая задержка между попытками (секунды)
        max_delay: Максимальная задержка между попытками (секунды)
    """
    
    DEFAULT_PORT = 9090
    
    def __init__(
        self,
        timeout: int = 10,
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
        self._request_id = 0
    
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
    
    def _next_request_id(self) -> int:
        """Получить следующий ID запроса."""
        self._request_id += 1
        return self._request_id
    
    def _build_request(
        self,
        method: str,
        params: Optional[Dict] = None
    ) -> str:
        """
        Построить JSON-RPC 2.0 запрос.
        
        Args:
            method: Имя метода
            params: Параметры метода (опционально)
            
        Returns:
            JSON строка запроса с переносом строки
        """
        request = {
            "jsonrpc": "2.0",
            "method": method,
            "id": self._next_request_id()
        }
        if params:
            request["params"] = params
        return json.dumps(request) + "\n"
    
    def _parse_response(
        self,
        response: str
    ) -> tuple[bool, Optional[Dict], Optional[str], Optional[int]]:
        """
        Распарсить JSON-RPC ответ.
        
        Args:
            response: JSON строка ответа
            
        Returns:
            Кортеж (success, result_data, error_message, error_code)
        """
        try:
            data = json.loads(response.strip())
            
            # Проверяем на JSON-RPC ошибку
            if "error" in data:
                error = data["error"]
                return (
                    False,
                    None,
                    error.get("message", str(error)),
                    error.get("code")
                )
            
            # Успешный ответ
            return (True, data.get("result"), None, None)
            
        except json.JSONDecodeError as e:
            return (False, None, f"Invalid JSON response: {e}", None)
    
    def _send_sync(
        self,
        ip: str,
        port: int,
        request: str
    ) -> tuple[bool, Optional[str], Optional[str]]:
        """
        Синхронная отправка запроса (для использования в executor).
        
        Args:
            ip: IP адрес устройства
            port: Порт устройства
            request: JSON-RPC запрос
            
        Returns:
            Кортеж (success, response_or_error, error_type)
        """
        sock = None
        try:
            sock = self._socket_factory()
            sock.settimeout(self.timeout)
            
            # Подключение
            sock.connect((ip, port))
            
            # Отправка запроса
            sock.sendall(request.encode('utf-8'))
            
            # Чтение ответа (читаем до переноса строки)
            response_parts = []
            sock.settimeout(5)
            
            while True:
                try:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    response_parts.append(chunk.decode('utf-8'))
                    
                    # Проверяем, есть ли полный JSON ответ
                    full_response = ''.join(response_parts)
                    if '\n' in full_response or '}' in full_response:
                        break
                except socket.timeout:
                    break
            
            response = ''.join(response_parts).strip()
            
            if not response:
                return (False, "Empty response from device", "EMPTY_RESPONSE")
            
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
        method: str,
        params: Optional[Dict] = None,
        port: int = None
    ) -> BarcoResult:
        """
        Отправить JSON-RPC команду с retry logic.
        
        Args:
            ip: IP адрес устройства
            method: JSON-RPC метод
            params: Параметры метода (опционально)
            port: Порт устройства (по умолчанию 9090)
            
        Returns:
            BarcoResult с результатом операции
        """
        if port is None:
            port = self.DEFAULT_PORT
        
        request = self._build_request(method, params)
        start_time = time.time()
        timestamps = []
        last_error = None
        last_error_type = None
        last_error_code = None
        
        for attempt in range(self.max_retries):
            attempt_start = time.time()
            attempt_timestamp = datetime.now().isoformat()
            
            logger.info(
                "barco_attempt_start",
                device_ip=ip,
                device_port=port,
                method=method,
                attempt=attempt + 1,
                max_attempts=self.max_retries
            )
            
            # Выполняем в executor
            loop = asyncio.get_event_loop()
            net_success, response_or_error, error_type = await loop.run_in_executor(
                None,
                self._send_sync,
                ip,
                port,
                request
            )
            
            attempt_duration = int((time.time() - attempt_start) * 1000)
            
            if net_success:
                # Парсим JSON-RPC ответ
                parse_success, result_data, parse_error, error_code = self._parse_response(
                    response_or_error
                )
                
                timestamps.append({
                    "attempt": attempt + 1,
                    "timestamp": attempt_timestamp,
                    "duration_ms": attempt_duration,
                    "success": parse_success,
                    "response": result_data if parse_success else None,
                    "error": parse_error
                })
                
                if parse_success:
                    logger.info(
                        "barco_command_success",
                        device_ip=ip,
                        device_port=port,
                        method=method,
                        attempt=attempt + 1,
                        duration_ms=attempt_duration,
                        result=result_data
                    )
                    
                    total_duration = int((time.time() - start_time) * 1000)
                    return BarcoResult(
                        success=True,
                        message="Command executed successfully",
                        method=method,
                        device_ip=ip,
                        device_port=port,
                        attempt_count=attempt + 1,
                        total_duration_ms=total_duration,
                        response_data=result_data,
                        timestamps=timestamps
                    )
                
                # JSON-RPC ошибка
                last_error = parse_error
                last_error_code = error_code
                last_error_type = "JSONRPC_ERROR"
            else:
                timestamps.append({
                    "attempt": attempt + 1,
                    "timestamp": attempt_timestamp,
                    "duration_ms": attempt_duration,
                    "success": False,
                    "error": response_or_error,
                    "error_type": error_type
                })
                
                last_error = response_or_error
                last_error_type = error_type
            
            logger.warning(
                "barco_attempt_failed",
                device_ip=ip,
                device_port=port,
                method=method,
                attempt=attempt + 1,
                error=last_error,
                error_type=last_error_type,
                duration_ms=attempt_duration
            )
            
            # Exponential backoff
            if attempt < self.max_retries - 1:
                delay = self._calculate_delay(attempt)
                logger.info(
                    "barco_retry_waiting",
                    device_ip=ip,
                    next_attempt=attempt + 2,
                    delay_seconds=delay
                )
                await asyncio.sleep(delay)
        
        # Все попытки исчерпаны
        total_duration = int((time.time() - start_time) * 1000)
        
        logger.error(
            "barco_command_failed",
            device_ip=ip,
            device_port=port,
            method=method,
            total_attempts=self.max_retries,
            total_duration_ms=total_duration,
            last_error=last_error,
            error_type=last_error_type
        )
        
        return BarcoResult(
            success=False,
            message=f"All {self.max_retries} attempts failed",
            method=method,
            device_ip=ip,
            device_port=port,
            attempt_count=self.max_retries,
            total_duration_ms=total_duration,
            error=last_error,
            error_code=last_error_code,
            error_type=last_error_type,
            timestamps=timestamps
        )
    
    async def power_on(self, ip: str, port: int = None) -> BarcoResult:
        """
        Включить Barco проектор.
        
        Args:
            ip: IP адрес проектора
            port: Порт (по умолчанию 9090)
            
        Returns:
            BarcoResult
        """
        return await self.send_command(
            ip=ip,
            method=BarcoCommand.POWER_ON.value,
            port=port
        )
    
    async def power_off(self, ip: str, port: int = None) -> BarcoResult:
        """
        Выключить Barco проектор.
        
        Args:
            ip: IP адрес проектора
            port: Порт (по умолчанию 9090)
            
        Returns:
            BarcoResult
        """
        return await self.send_command(
            ip=ip,
            method=BarcoCommand.POWER_OFF.value,
            port=port
        )
    
    async def get_power_state(self, ip: str, port: int = None) -> BarcoResult:
        """
        Получить состояние питания Barco проектора.
        
        Args:
            ip: IP адрес проектора
            port: Порт (по умолчанию 9090)
            
        Returns:
            BarcoResult с power_state в response_data
        """
        return await self.send_command(
            ip=ip,
            method=BarcoCommand.POWER_STATE_GET.value,
            port=port
        )
    
    async def get_lamp_hours(self, ip: str, port: int = None) -> BarcoResult:
        """
        Получить время работы лампы.
        
        Args:
            ip: IP адрес проектора
            port: Порт (по умолчанию 9090)
            
        Returns:
            BarcoResult с lamp_hours в response_data
        """
        return await self.send_command(
            ip=ip,
            method=BarcoCommand.LAMP_HOURS.value,
            port=port
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
        # Создаём клиент
        client = BarcoClient(
            timeout=10,
            max_retries=3,
            base_delay=30
        )
        
        # Проверяем доступность
        ip = "192.168.2.95"
        print(f"\nПроверка доступности {ip}:9090...")
        is_reachable = await client.check_reachable(ip)
        print(f"Устройство доступно: {is_reachable}")
        
        if is_reachable:
            # Получаем статус
            print(f"\nПолучение статуса питания...")
            result = await client.get_power_state(ip)
            
            print("\nРезультат:")
            print(f"  Успех: {result.success}")
            print(f"  Попыток: {result.attempt_count}")
            print(f"  Время: {result.total_duration_ms}ms")
            if result.response_data:
                print(f"  Данные: {result.response_data}")
            if result.error:
                print(f"  Ошибка: {result.error}")
            
            # Включаем проектор
            print(f"\nВключение проектора...")
            result = await client.power_on(ip)
            
            # JSON лог
            print("\nJSON лог:")
            print(result.to_json())
    
    asyncio.run(main())
