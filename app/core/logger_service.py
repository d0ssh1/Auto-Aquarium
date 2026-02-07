"""
Logger Service — Структурированное логирование в JSON.

Модуль для централизованного логирования с поддержкой:
- JSON формат для машинного анализа
- Консольный вывод для отладки
- Запись в файлы с ротацией
- Специализированные логгеры для действий устройств

Использование:
    from logger_service import get_logger, log_device_action
    
    logger = get_logger("my_module")
    logger.info("event_name", key1="value1", key2=123)
    
    log_device_action(
        device_id="optoma_2.64",
        action="POWER_ON",
        success=True,
        duration_ms=250
    )
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, Union
from dataclasses import dataclass, asdict
from enum import Enum
import threading

import structlog


class LogLevel(str, Enum):
    """Уровни логирования."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class ActionType(str, Enum):
    """Типы действий для логирования."""
    POWER_ON = "POWER_ON"
    POWER_OFF = "POWER_OFF"
    STATUS_CHECK = "STATUS_CHECK"
    PING = "PING"
    CONNECT = "CONNECT"
    DISCONNECT = "DISCONNECT"
    RETRY = "RETRY"
    SCHEDULE_START = "SCHEDULE_START"
    SCHEDULE_COMPLETE = "SCHEDULE_COMPLETE"
    CONFIG_RELOAD = "CONFIG_RELOAD"
    ERROR = "ERROR"


class TriggerType(str, Enum):
    """Типы триггеров действий."""
    SCHEDULED = "scheduled"
    MANUAL = "manual"
    API = "api"
    STARTUP = "startup"
    WATCHDOG = "watchdog"


@dataclass
class DeviceActionLog:
    """
    Структура лога действия устройства.
    
    Полностью типизированный формат для анализа.
    """
    timestamp: str
    device_id: str
    device_name: Optional[str]
    device_ip: Optional[str]
    action: str
    trigger: str
    success: bool
    attempt: int
    duration_ms: int
    error: Optional[str] = None
    error_type: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Конвертировать в словарь."""
        result = asdict(self)
        # Убираем None значения для компактности
        return {k: v for k, v in result.items() if v is not None}
    
    def to_json(self) -> str:
        """Конвертировать в JSON строку."""
        return json.dumps(self.to_dict(), ensure_ascii=False)


class JSONFileHandler(logging.Handler):
    """
    Handler для записи JSON логов в файл.
    
    Поддерживает:
    - Append режим (дописывание)
    - Thread-safe операции
    - Автоматическое создание директории
    """
    
    def __init__(
        self,
        filename: str,
        mode: str = "a",
        encoding: str = "utf-8"
    ):
        """
        Инициализация handler'а.
        
        Args:
            filename: Путь к файлу
            mode: Режим открытия файла
            encoding: Кодировка файла
        """
        super().__init__()
        self.filename = Path(filename)
        self.mode = mode
        self.encoding = encoding
        self._lock = threading.Lock()
        
        # Создаём директорию если нужно
        self.filename.parent.mkdir(parents=True, exist_ok=True)
    
    def emit(self, record: logging.LogRecord) -> None:
        """
        Записать лог запись.
        
        Args:
            record: LogRecord
        """
        try:
            msg = self.format(record)
            with self._lock:
                with open(self.filename, self.mode, encoding=self.encoding) as f:
                    f.write(msg + "\n")
        except Exception:
            self.handleError(record)


class LoggerService:
    """
    Сервис централизованного логирования.
    
    Настраивает structlog для структурированного логирования
    с выводом в консоль и файлы.
    
    Attributes:
        log_dir: Директория для логов
        console_level: Уровень для консоли
        file_level: Уровень для файлов
    """
    
    def __init__(
        self,
        log_dir: str = "logs",
        console_level: LogLevel = LogLevel.INFO,
        file_level: LogLevel = LogLevel.DEBUG,
        json_logs: bool = True
    ):
        """
        Инициализация сервиса.
        
        Args:
            log_dir: Директория для логов
            console_level: Уровень логирования в консоль
            file_level: Уровень логирования в файлы
            json_logs: Писать JSON логи
        """
        self.log_dir = Path(log_dir)
        self.console_level = console_level
        self.file_level = file_level
        self.json_logs = json_logs
        
        self._configured = False
        self._action_file: Optional[Path] = None
    
    def configure(self) -> None:
        """Настроить систему логирования."""
        if self._configured:
            return
        
        # Создаём директорию логов
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # Файл для action логов
        self._action_file = self.log_dir / "actions.jsonl"
        
        # Настраиваем стандартный logging
        logging.basicConfig(
            format="%(message)s",
            stream=sys.stdout,
            level=getattr(logging, self.console_level.value)
        )
        
        # Настраиваем structlog
        processors = [
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
        ]
        
        if self.json_logs:
            processors.append(structlog.processors.JSONRenderer(ensure_ascii=False))
        else:
            processors.append(structlog.dev.ConsoleRenderer(colors=True))
        
        structlog.configure(
            processors=processors,
            wrapper_class=structlog.stdlib.BoundLogger,
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )
        
        self._configured = True
    
    def get_logger(self, name: str = None) -> structlog.BoundLogger:
        """
        Получить логгер.
        
        Args:
            name: Имя логгера
            
        Returns:
            structlog.BoundLogger
        """
        if not self._configured:
            self.configure()
        
        return structlog.get_logger(name)
    
    def log_action(
        self,
        device_id: str,
        action: Union[ActionType, str],
        success: bool,
        trigger: Union[TriggerType, str] = TriggerType.MANUAL,
        duration_ms: int = 0,
        attempt: int = 1,
        device_name: Optional[str] = None,
        device_ip: Optional[str] = None,
        error: Optional[str] = None,
        error_type: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ) -> DeviceActionLog:
        """
        Залогировать действие с устройством.
        
        Args:
            device_id: ID устройства
            action: Тип действия
            success: Успешность
            trigger: Триггер действия
            duration_ms: Длительность в мс
            attempt: Номер попытки
            device_name: Имя устройства
            device_ip: IP устройства
            error: Текст ошибки
            error_type: Тип ошибки
            details: Дополнительные данные
            
        Returns:
            DeviceActionLog
        """
        if not self._configured:
            self.configure()
        
        # Нормализуем enum'ы
        action_str = action.value if isinstance(action, ActionType) else action
        trigger_str = trigger.value if isinstance(trigger, TriggerType) else trigger
        
        # Создаём запись
        log_entry = DeviceActionLog(
            timestamp=datetime.now().isoformat(),
            device_id=device_id,
            device_name=device_name,
            device_ip=device_ip,
            action=action_str,
            trigger=trigger_str,
            success=success,
            attempt=attempt,
            duration_ms=duration_ms,
            error=error,
            error_type=error_type,
            details=details
        )
        
        # Пишем в общий лог
        logger = self.get_logger("device_action")
        
        if success:
            logger.info(
                "device_action",
                **log_entry.to_dict()
            )
        else:
            logger.error(
                "device_action",
                **log_entry.to_dict()
            )
        
        # Пишем в action log файл
        if self._action_file:
            try:
                with open(self._action_file, "a", encoding="utf-8") as f:
                    f.write(log_entry.to_json() + "\n")
            except Exception as e:
                logger.error("action_log_write_error", error=str(e))
        
        return log_entry
    
    def log_schedule_event(
        self,
        event: str,
        action: str,
        total_devices: int = 0,
        successful: int = 0,
        failed: int = 0,
        duration_ms: int = 0,
        details: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Залогировать событие расписания.
        
        Args:
            event: Тип события (start/complete)
            action: Действие (turn_on/turn_off)
            total_devices: Всего устройств
            successful: Успешных
            failed: Неуспешных
            duration_ms: Длительность
            details: Дополнительные данные
        """
        if not self._configured:
            self.configure()
        
        logger = self.get_logger("scheduler")
        
        log_data = {
            "timestamp": datetime.now().isoformat(),
            "event": event,
            "action": action,
            "total_devices": total_devices,
            "successful": successful,
            "failed": failed,
            "duration_ms": duration_ms
        }
        
        if details:
            log_data["details"] = details
        
        logger.info("schedule_event", **log_data)
    
    def log_api_request(
        self,
        method: str,
        path: str,
        status_code: int,
        duration_ms: int,
        client_ip: Optional[str] = None,
        error: Optional[str] = None
    ) -> None:
        """
        Залогировать API запрос.
        
        Args:
            method: HTTP метод
            path: Путь запроса
            status_code: Код ответа
            duration_ms: Длительность
            client_ip: IP клиента
            error: Ошибка если есть
        """
        if not self._configured:
            self.configure()
        
        logger = self.get_logger("api")
        
        log_data = {
            "timestamp": datetime.now().isoformat(),
            "method": method,
            "path": path,
            "status_code": status_code,
            "duration_ms": duration_ms
        }
        
        if client_ip:
            log_data["client_ip"] = client_ip
        
        if error:
            log_data["error"] = error
            logger.error("api_request", **log_data)
        else:
            logger.info("api_request", **log_data)


# Глобальный экземпляр
_logger_service: Optional[LoggerService] = None


def get_logger_service(
    log_dir: str = "logs",
    console_level: LogLevel = LogLevel.INFO
) -> LoggerService:
    """
    Получить глобальный сервис логирования.
    
    Args:
        log_dir: Директория логов
        console_level: Уровень для консоли
        
    Returns:
        LoggerService
    """
    global _logger_service
    if _logger_service is None:
        _logger_service = LoggerService(
            log_dir=log_dir,
            console_level=console_level
        )
        _logger_service.configure()
    return _logger_service


def get_logger(name: str = None) -> structlog.BoundLogger:
    """
    Получить логгер.
    
    Удобная функция для быстрого получения логгера.
    
    Args:
        name: Имя логгера
        
    Returns:
        structlog.BoundLogger
    """
    return get_logger_service().get_logger(name)


def log_device_action(
    device_id: str,
    action: Union[ActionType, str],
    success: bool,
    **kwargs
) -> DeviceActionLog:
    """
    Залогировать действие устройства.
    
    Удобная функция для быстрого логирования.
    
    Args:
        device_id: ID устройства
        action: Тип действия
        success: Успешность
        **kwargs: Дополнительные параметры
        
    Returns:
        DeviceActionLog
    """
    return get_logger_service().log_action(
        device_id=device_id,
        action=action,
        success=success,
        **kwargs
    )


# Пример использования:
if __name__ == "__main__":
    # Инициализация
    service = get_logger_service(log_dir="logs")
    
    # Получаем логгер
    logger = get_logger("example")
    
    # Обычное логирование
    logger.info("application_start", version="1.0.0")
    logger.debug("debug_message", data={"key": "value"})
    logger.warning("something_suspicious", ip="192.168.1.1")
    
    # Логирование действий устройств
    print("\n=== Device Actions ===")
    
    # Успешное действие
    log_entry = log_device_action(
        device_id="optoma_2.64",
        device_name="Optoma 2.64",
        device_ip="192.168.2.64",
        action=ActionType.POWER_ON,
        trigger=TriggerType.MANUAL,
        success=True,
        attempt=1,
        duration_ms=250,
        details={"response": "OK"}
    )
    print(f"Action logged: {log_entry.to_json()}")
    
    # Неуспешное действие с retry
    log_entry = log_device_action(
        device_id="barco_95",
        device_name="Barco 95",
        device_ip="192.168.2.95",
        action=ActionType.POWER_ON,
        trigger=TriggerType.SCHEDULED,
        success=False,
        attempt=3,
        duration_ms=90500,
        error="Connection timeout after 3 attempts",
        error_type="TIMEOUT"
    )
    print(f"Failed action logged: {log_entry.to_json()}")
    
    # Событие расписания
    print("\n=== Schedule Event ===")
    service.log_schedule_event(
        event="complete",
        action="turn_on",
        total_devices=45,
        successful=43,
        failed=2,
        duration_ms=125000,
        details={
            "failed_devices": ["optoma_51", "barco_97"]
        }
    )
    
    print("\n✅ Logs written to ./logs/")
    print("   - actions.jsonl (device actions)")
