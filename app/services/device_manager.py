"""
Device Manager — Orchestrator для управления устройствами.

Модуль для параллельного включения/выключения устройств с retry logic,
обработкой ошибок и генерацией отчётов.

Использование:
    manager = DeviceManager.from_config("config.json")
    report = await manager.turn_on_all()
    print(report.to_summary())
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any, Callable

import structlog
from pydantic import BaseModel, Field

# Local imports
from app.core.device_registry import DeviceRegistry, Device, DeviceType, get_registry
from app.protocols.telnet_client import TelnetClient
from app.protocols.barco_client import BarcoClient

logger = structlog.get_logger()


class ActionType(str, Enum):
    """Типы действий."""
    TURN_ON = "TURN_ON"
    TURN_OFF = "TURN_OFF"


class ExecutionStatus(str, Enum):
    """Статус выполнения."""
    SUCCESS = "SUCCESS"  # Все устройства включены
    PARTIAL = "PARTIAL"  # Большинство работают
    FAILED = "FAILED"  # Критическая ошибка


@dataclass
class DeviceResult:
    """Результат операции с устройством."""
    device_id: str
    device_name: str
    device_ip: str
    device_type: str
    success: bool
    attempts: int
    duration_ms: int
    error: Optional[str] = None
    error_type: Optional[str] = None
    response: Optional[str] = None


class ExecutionReport(BaseModel):
    """
    Отчёт о выполнении операции.
    
    Attributes:
        timestamp: Время выполнения
        action: Тип действия
        total_devices: Общее количество устройств
        successful: Успешных
        failed: Неуспешных
        devices_with_errors: Список устройств с ошибками
        retry_count: Общее количество повторных попыток
        duration_seconds: Общая длительность
        status: Итоговый статус
    """
    timestamp: datetime
    action: str
    total_devices: int = 0
    successful: int = 0
    failed: int = 0
    devices_with_errors: List[str] = Field(default_factory=list)
    devices_with_retries: List[str] = Field(default_factory=list)
    retry_count: int = 0
    duration_seconds: float = 0.0
    status: str = ExecutionStatus.SUCCESS.value
    device_results: List[Dict[str, Any]] = Field(default_factory=list)
    
    class Config:
        use_enum_values = True
    
    def to_summary(self) -> str:
        """Генерировать текстовую сводку."""
        lines = [
            f"EXECUTION REPORT — {self.timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 50,
            f"Action: {self.action}",
            f"Total Devices: {self.total_devices}",
            f"✅ Successful: {self.successful} ({self.successful/max(self.total_devices, 1)*100:.1f}%)",
        ]
        
        if self.devices_with_retries:
            lines.append(f"⚠️ Required Retries: {len(self.devices_with_retries)}")
        
        if self.failed > 0:
            lines.append(f"❌ Failed: {self.failed}")
            for device_id in self.devices_with_errors:
                lines.append(f"   - {device_id}")
        
        lines.append(f"\nDuration: {self.duration_seconds:.1f} seconds")
        lines.append(f"Status: {self.status}")
        
        return "\n".join(lines)
    
    def to_dict(self) -> Dict[str, Any]:
        """Конвертировать в словарь."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "action": self.action,
            "total_devices": self.total_devices,
            "successful": self.successful,
            "failed": self.failed,
            "devices_with_errors": self.devices_with_errors,
            "devices_with_retries": self.devices_with_retries,
            "retry_count": self.retry_count,
            "duration_seconds": self.duration_seconds,
            "status": self.status,
            "success_rate": self.successful / max(self.total_devices, 1)
        }


class RetryPolicy(BaseModel):
    """Политика повторных попыток."""
    max_attempts: int = 3
    base_interval_sec: int = 30
    backoff_multiplier: float = 2.0


class DeviceManager:
    """
    Менеджер устройств — orchestrator для массовых операций.
    
    Поддерживает:
    - Параллельное выполнение операций
    - Retry logic с exponential backoff
    - Graceful degradation (продолжать при ошибках отдельных устройств)
    - Детальное логирование и отчёты
    
    Attributes:
        registry: Реестр устройств
        retry_policy: Политика повторных попыток
        telnet_client: Клиент для Optoma
        barco_client: Клиент для Barco
    """
    
    def __init__(
        self,
        registry: Optional[DeviceRegistry] = None,
        retry_policy: Optional[RetryPolicy] = None,
        telnet_client: Optional[TelnetClient] = None,
        barco_client: Optional[BarcoClient] = None,
        parallel_limit: int = 10
    ):
        """
        Инициализация менеджера.
        
        Args:
            registry: Реестр устройств
            retry_policy: Политика повторов
            telnet_client: Клиент Telnet
            barco_client: Клиент Barco
            parallel_limit: Максимум параллельных операций
        """
        self.registry = registry or get_registry()
        self.retry_policy = retry_policy or RetryPolicy()
        self._telnet_client = telnet_client
        self._barco_client = barco_client
        self._parallel_limit = parallel_limit
        
        # Lazy initialization
        self._telnet_client_initialized = False
        self._barco_client_initialized = False
    
    @classmethod
    def from_config(cls, config_path: str = "config.json") -> "DeviceManager":
        """
        Создать менеджер из конфигурации.
        
        Args:
            config_path: Путь к config.json
            
        Returns:
            DeviceManager
        """
        import json
        
        registry = DeviceRegistry.from_config(config_path)
        
        # Загружаем retry policy
        retry_policy = RetryPolicy()
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "retry_policy" in data:
                retry_policy = RetryPolicy(**data["retry_policy"])
            elif "retry" in data:
                retry_policy = RetryPolicy(**data["retry"])
        except Exception:
            pass
        
        return cls(registry=registry, retry_policy=retry_policy)
    
    @property
    def telnet_client(self) -> TelnetClient:
        """Получить Telnet клиент (lazy init)."""
        if not self._telnet_client_initialized:
            self._telnet_client = TelnetClient(
                timeout=10,
                max_retries=self.retry_policy.max_attempts,
                base_delay=self.retry_policy.base_interval_sec
            )
            self._telnet_client_initialized = True
        return self._telnet_client
    
    @property
    def barco_client(self) -> BarcoClient:
        """Получить Barco клиент (lazy init)."""
        if not self._barco_client_initialized:
            self._barco_client = BarcoClient(
                timeout=10,
                max_retries=self.retry_policy.max_attempts,
                base_delay=self.retry_policy.base_interval_sec
            )
            self._barco_client_initialized = True
        return self._barco_client
    
    async def _execute_device_action(
        self,
        device: Device,
        action: ActionType
    ) -> DeviceResult:
        """
        Выполнить действие с одним устройством.
        
        Args:
            device: Устройство
            action: Тип действия
            
        Returns:
            DeviceResult
        """
        start_time = time.time()
        
        logger.info(
            "device_action_start",
            device_id=device.id,
            device_ip=device.ip,
            device_type=device.device_type,
            action=action.value
        )
        
        try:
            # Выбираем протокол по типу устройства
            if device.device_type == DeviceType.OPTOMA_TELNET:
                if action == ActionType.TURN_ON:
                    result = await self.telnet_client.power_on(device.ip, device.port)
                else:
                    result = await self.telnet_client.power_off(device.ip, device.port)
                
                duration_ms = result.total_duration_ms
                return DeviceResult(
                    device_id=device.id,
                    device_name=device.name,
                    device_ip=device.ip,
                    device_type=device.device_type,
                    success=result.success,
                    attempts=result.attempt_count,
                    duration_ms=duration_ms,
                    error=result.error,
                    error_type=result.error_type,
                    response=result.response
                )
            
            elif device.device_type == DeviceType.BARCO_JSONRPC:
                if action == ActionType.TURN_ON:
                    result = await self.barco_client.power_on(device.ip, device.port)
                else:
                    result = await self.barco_client.power_off(device.ip, device.port)
                
                duration_ms = result.total_duration_ms
                return DeviceResult(
                    device_id=device.id,
                    device_name=device.name,
                    device_ip=device.ip,
                    device_type=device.device_type,
                    success=result.success,
                    attempts=result.attempt_count,
                    duration_ms=duration_ms,
                    error=result.error,
                    error_type=result.error_type,
                    response=str(result.response_data) if result.response_data else None
                )
            
            elif device.device_type == DeviceType.CUBES_CUSTOM:
                # TODO: Implement Cubes client
                logger.warning(
                    "device_type_not_implemented",
                    device_id=device.id,
                    device_type=device.device_type
                )
                duration_ms = int((time.time() - start_time) * 1000)
                return DeviceResult(
                    device_id=device.id,
                    device_name=device.name,
                    device_ip=device.ip,
                    device_type=device.device_type,
                    success=False,
                    attempts=1,
                    duration_ms=duration_ms,
                    error="Protocol not implemented",
                    error_type="NOT_IMPLEMENTED"
                )
            
            elif device.device_type == DeviceType.EXPOSITION_PC:
                # Exposition PCs не управляются напрямую, только ping
                logger.debug(
                    "device_skip_exposition_pc",
                    device_id=device.id
                )
                duration_ms = int((time.time() - start_time) * 1000)
                return DeviceResult(
                    device_id=device.id,
                    device_name=device.name,
                    device_ip=device.ip,
                    device_type=device.device_type,
                    success=True,  # Считаем успешным (пропускаем)
                    attempts=0,
                    duration_ms=duration_ms,
                    response="Skipped (no direct control)"
                )
            
            else:
                logger.warning(
                    "device_unknown_type",
                    device_id=device.id,
                    device_type=device.device_type
                )
                duration_ms = int((time.time() - start_time) * 1000)
                return DeviceResult(
                    device_id=device.id,
                    device_name=device.name,
                    device_ip=device.ip,
                    device_type=device.device_type,
                    success=False,
                    attempts=1,
                    duration_ms=duration_ms,
                    error=f"Unknown device type: {device.device_type}",
                    error_type="UNKNOWN_TYPE"
                )
        
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.error(
                "device_action_exception",
                device_id=device.id,
                action=action.value,
                error=str(e)
            )
            return DeviceResult(
                device_id=device.id,
                device_name=device.name,
                device_ip=device.ip,
                device_type=device.device_type,
                success=False,
                attempts=1,
                duration_ms=duration_ms,
                error=str(e),
                error_type="EXCEPTION"
            )
    
    async def _execute_batch(
        self,
        devices: List[Device],
        action: ActionType,
        parallel: bool = True
    ) -> List[DeviceResult]:
        """
        Выполнить действие над группой устройств.
        
        Args:
            devices: Список устройств
            action: Тип действия
            parallel: Выполнять параллельно
            
        Returns:
            Список результатов
        """
        if not devices:
            return []
        
        if parallel:
            # Ограничиваем параллельность через семафор
            semaphore = asyncio.Semaphore(self._parallel_limit)
            
            async def limited_action(device: Device) -> DeviceResult:
                async with semaphore:
                    return await self._execute_device_action(device, action)
            
            tasks = [limited_action(device) for device in devices]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Обрабатываем исключения
            processed_results = []
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    device = devices[i]
                    processed_results.append(DeviceResult(
                        device_id=device.id,
                        device_name=device.name,
                        device_ip=device.ip,
                        device_type=device.device_type,
                        success=False,
                        attempts=1,
                        duration_ms=0,
                        error=str(result),
                        error_type="EXCEPTION"
                    ))
                else:
                    processed_results.append(result)
            
            return processed_results
        else:
            # Последовательное выполнение
            results = []
            for device in devices:
                result = await self._execute_device_action(device, action)
                results.append(result)
            return results
    
    def _build_report(
        self,
        action: ActionType,
        results: List[DeviceResult],
        duration_seconds: float
    ) -> ExecutionReport:
        """
        Построить отчёт о выполнении.
        
        Args:
            action: Тип действия
            results: Результаты
            duration_seconds: Общая длительность
            
        Returns:
            ExecutionReport
        """
        successful = [r for r in results if r.success]
        failed = [r for r in results if not r.success]
        with_retries = [r for r in results if r.attempts > 1]
        total_retries = sum(max(0, r.attempts - 1) for r in results)
        
        # Определяем статус
        success_rate = len(successful) / max(len(results), 1)
        if success_rate == 1.0:
            status = ExecutionStatus.SUCCESS
        elif success_rate >= 0.8:
            status = ExecutionStatus.PARTIAL
        else:
            status = ExecutionStatus.FAILED
        
        return ExecutionReport(
            timestamp=datetime.now(),
            action=action.value,
            total_devices=len(results),
            successful=len(successful),
            failed=len(failed),
            devices_with_errors=[r.device_id for r in failed],
            devices_with_retries=[r.device_id for r in with_retries],
            retry_count=total_retries,
            duration_seconds=duration_seconds,
            status=status.value,
            device_results=[
                {
                    "device_id": r.device_id,
                    "device_name": r.device_name,
                    "success": r.success,
                    "attempts": r.attempts,
                    "duration_ms": r.duration_ms,
                    "error": r.error
                }
                for r in results
            ]
        )
    
    async def turn_on_all(
        self,
        parallel: bool = True,
        device_types: Optional[List[DeviceType]] = None
    ) -> ExecutionReport:
        """
        Включить все устройства.
        
        Args:
            parallel: Выполнять параллельно
            device_types: Фильтр по типам (None = все)
            
        Returns:
            ExecutionReport
        """
        start_time = time.time()
        
        # Получаем устройства
        devices = self.registry.get_devices(enabled_only=True)
        
        # Фильтруем по типам если указано
        if device_types:
            devices = [d for d in devices if d.device_type in device_types]
        
        # Фильтруем не-управляемые устройства
        controllable_devices = [
            d for d in devices
            if d.device_type in [
                DeviceType.OPTOMA_TELNET,
                DeviceType.BARCO_JSONRPC,
                DeviceType.CUBES_CUSTOM
            ]
        ]
        
        logger.info(
            "turn_on_all_start",
            total_devices=len(devices),
            controllable_devices=len(controllable_devices),
            parallel=parallel
        )
        
        # Выполняем
        results = await self._execute_batch(
            controllable_devices,
            ActionType.TURN_ON,
            parallel=parallel
        )
        
        duration = time.time() - start_time
        report = self._build_report(ActionType.TURN_ON, results, duration)
        
        logger.info(
            "turn_on_all_complete",
            **report.to_dict()
        )
        
        return report
    
    async def turn_off_all(
        self,
        parallel: bool = True,
        device_types: Optional[List[DeviceType]] = None
    ) -> ExecutionReport:
        """
        Выключить все устройства.
        
        Args:
            parallel: Выполнять параллельно
            device_types: Фильтр по типам (None = все)
            
        Returns:
            ExecutionReport
        """
        start_time = time.time()
        
        # Получаем устройства
        devices = self.registry.get_devices(enabled_only=True)
        
        # Фильтруем по типам если указано
        if device_types:
            devices = [d for d in devices if d.device_type in device_types]
        
        # Фильтруем не-управляемые устройства
        controllable_devices = [
            d for d in devices
            if d.device_type in [
                DeviceType.OPTOMA_TELNET,
                DeviceType.BARCO_JSONRPC,
                DeviceType.CUBES_CUSTOM
            ]
        ]
        
        logger.info(
            "turn_off_all_start",
            total_devices=len(devices),
            controllable_devices=len(controllable_devices),
            parallel=parallel
        )
        
        # Выполняем
        results = await self._execute_batch(
            controllable_devices,
            ActionType.TURN_OFF,
            parallel=parallel
        )
        
        duration = time.time() - start_time
        report = self._build_report(ActionType.TURN_OFF, results, duration)
        
        logger.info(
            "turn_off_all_complete",
            **report.to_dict()
        )
        
        return report
    
    async def turn_on_device(self, device_id: str) -> DeviceResult:
        """
        Включить одно устройство.
        
        Args:
            device_id: ID устройства
            
        Returns:
            DeviceResult
        """
        device = self.registry.get_device(device_id)
        if not device:
            return DeviceResult(
                device_id=device_id,
                device_name="Unknown",
                device_ip="",
                device_type="",
                success=False,
                attempts=0,
                duration_ms=0,
                error=f"Device not found: {device_id}",
                error_type="NOT_FOUND"
            )
        
        return await self._execute_device_action(device, ActionType.TURN_ON)
    
    async def turn_off_device(self, device_id: str) -> DeviceResult:
        """
        Выключить одно устройство.
        
        Args:
            device_id: ID устройства
            
        Returns:
            DeviceResult
        """
        device = self.registry.get_device(device_id)
        if not device:
            return DeviceResult(
                device_id=device_id,
                device_name="Unknown",
                device_ip="",
                device_type="",
                success=False,
                attempts=0,
                duration_ms=0,
                error=f"Device not found: {device_id}",
                error_type="NOT_FOUND"
            )
        
        return await self._execute_device_action(device, ActionType.TURN_OFF)
    
    async def turn_on_group(
        self,
        group_id: str,
        parallel: bool = True
    ) -> ExecutionReport:
        """
        Включить устройства группы.
        
        Args:
            group_id: ID группы
            parallel: Выполнять параллельно
            
        Returns:
            ExecutionReport
        """
        start_time = time.time()
        
        devices = self.registry.get_by_group(group_id, enabled_only=True)
        
        # Фильтруем управляемые
        controllable = [
            d for d in devices
            if d.device_type in [
                DeviceType.OPTOMA_TELNET,
                DeviceType.BARCO_JSONRPC,
                DeviceType.CUBES_CUSTOM
            ]
        ]
        
        logger.info(
            "turn_on_group_start",
            group_id=group_id,
            devices=len(controllable)
        )
        
        results = await self._execute_batch(controllable, ActionType.TURN_ON, parallel)
        
        duration = time.time() - start_time
        return self._build_report(ActionType.TURN_ON, results, duration)
    
    async def turn_off_group(
        self,
        group_id: str,
        parallel: bool = True
    ) -> ExecutionReport:
        """
        Выключить устройства группы.
        
        Args:
            group_id: ID группы
            parallel: Выполнять параллельно
            
        Returns:
            ExecutionReport
        """
        start_time = time.time()
        
        devices = self.registry.get_by_group(group_id, enabled_only=True)
        
        # Фильтруем управляемые
        controllable = [
            d for d in devices
            if d.device_type in [
                DeviceType.OPTOMA_TELNET,
                DeviceType.BARCO_JSONRPC,
                DeviceType.CUBES_CUSTOM
            ]
        ]
        
        logger.info(
            "turn_off_group_start",
            group_id=group_id,
            devices=len(controllable)
        )
        
        results = await self._execute_batch(controllable, ActionType.TURN_OFF, parallel)
        
        duration = time.time() - start_time
        return self._build_report(ActionType.TURN_OFF, results, duration)


# Global instance
_device_manager: Optional[DeviceManager] = None


def get_device_manager(config_path: str = "config.json") -> DeviceManager:
    """
    Получить глобальный экземпляр менеджера.
    
    Args:
        config_path: Путь к конфигурации
        
    Returns:
        DeviceManager
    """
    global _device_manager
    if _device_manager is None:
        _device_manager = DeviceManager.from_config(config_path)
    return _device_manager


# Пример использования:
if __name__ == "__main__":
    import asyncio
    
    async def main():
        # Создаём менеджер
        manager = DeviceManager.from_config("config.json")
        
        print("=== Device Manager Demo ===\n")
        
        # Информация о реестре
        stats = manager.registry.get_stats()
        print(f"Total devices: {stats['total_devices']}")
        print(f"Enabled: {stats['enabled_devices']}")
        print(f"By type: {stats['by_type']}")
        
        # Включаем все проекторы
        print("\n--- Turning ON all projectors ---")
        report = await manager.turn_on_all(
            parallel=True,
            device_types=[DeviceType.OPTOMA_TELNET, DeviceType.BARCO_JSONRPC]
        )
        
        print(report.to_summary())
        
        # Включаем одно устройство
        print("\n--- Turning ON single device ---")
        result = await manager.turn_on_device("optoma_2.64")
        print(f"Device: {result.device_id}")
        print(f"Success: {result.success}")
        print(f"Attempts: {result.attempts}")
        if result.error:
            print(f"Error: {result.error}")
    
    asyncio.run(main())
