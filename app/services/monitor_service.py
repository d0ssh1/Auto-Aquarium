"""
Monitor Service — Периодический мониторинг и алерты.

Модуль для отслеживания состояния устройств:
- Периодическая проверка статуса
- Детекция "упавших" устройств
- Алерты при массовых сбоях
- Интеграция с Zabbix

Использование:
    monitor = MonitorService.from_config("config.json")
    await monitor.check_all_devices()
"""

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, List, Dict, Any, Set

import structlog
from pydantic import BaseModel, Field

# Local imports
from app.core.device_registry import DeviceRegistry, Device, get_registry
from app.protocols.device_monitor import DeviceMonitor, DeviceStatus, DeviceState

logger = structlog.get_logger()


class AlertLevel(str, Enum):
    """Уровни алертов."""
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    RED_ALERT = "RED_ALERT"


class AlertType(str, Enum):
    """Типы алертов."""
    DEVICE_DOWN = "device_down"
    DEVICE_RECOVERED = "device_recovered"
    MULTIPLE_DEVICES_DOWN = "multiple_devices_down"
    NETWORK_ISSUE = "network_issue"
    THRESHOLD_BREACH = "threshold_breach"


@dataclass
class Alert:
    """Алерт о проблеме."""
    timestamp: datetime
    level: AlertLevel
    alert_type: AlertType
    message: str
    device_ids: List[str] = field(default_factory=list)
    details: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "level": self.level.value,
            "type": self.alert_type.value,
            "message": self.message,
            "device_ids": self.device_ids,
            "details": self.details
        }


@dataclass
class DeviceHealthRecord:
    """Запись о состоянии устройства."""
    device_id: str
    device_ip: str
    state: DeviceState
    last_check: datetime
    last_online: Optional[datetime] = None
    consecutive_failures: int = 0
    error_message: Optional[str] = None


class MonitoringConfig(BaseModel):
    """Конфигурация мониторинга."""
    status_check_interval_sec: int = 300
    alert_threshold: float = 0.8  # Процент онлайн устройств
    consecutive_failures_alert: int = 2
    multi_device_alert_count: int = 2
    network_issue_threshold: int = 5  # Если столько упало, возможна проблема с сетью


class MonitorService:
    """
    Сервис мониторинга устройств.
    
    Отслеживает состояние устройств и генерирует алерты
    при обнаружении проблем.
    
    Attributes:
        registry: Реестр устройств
        config: Конфигурация мониторинга
        device_monitor: Монитор устройств
    """
    
    def __init__(
        self,
        registry: Optional[DeviceRegistry] = None,
        config: Optional[MonitoringConfig] = None,
        device_monitor: Optional[DeviceMonitor] = None
    ):
        """
        Инициализация сервиса.
        
        Args:
            registry: Реестр устройств
            config: Конфигурация
            device_monitor: Монитор устройств
        """
        self.registry = registry or get_registry()
        self.config = config or MonitoringConfig()
        self.device_monitor = device_monitor or DeviceMonitor()
        
        # Состояние устройств
        self._health_records: Dict[str, DeviceHealthRecord] = {}
        self._previous_online_set: Set[str] = set()
        self._alerts: List[Alert] = []
        self._last_check: Optional[datetime] = None
        self._running = False
    
    @classmethod
    def from_config(cls, config_path: str = "config.json") -> "MonitorService":
        """
        Создать сервис из конфигурации.
        
        Args:
            config_path: Путь к config.json
            
        Returns:
            MonitorService
        """
        import json
        
        registry = DeviceRegistry.from_config(config_path)
        config = MonitoringConfig()
        
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "monitoring" in data:
                config = MonitoringConfig(**data["monitoring"])
        except Exception:
            pass
        
        return cls(registry=registry, config=config)
    
    async def check_device(self, device: Device) -> DeviceHealthRecord:
        """
        Проверить одно устройство.
        
        Args:
            device: Устройство
            
        Returns:
            DeviceHealthRecord
        """
        # Получаем предыдущую запись
        prev_record = self._health_records.get(device.id)
        
        # Выполняем проверку
        status = await self.device_monitor.check_device(
            ip=device.ip,
            port=device.port or device.default_port
        )
        
        now = datetime.now()
        
        # Формируем новую запись
        if status.state == DeviceState.ONLINE:
            record = DeviceHealthRecord(
                device_id=device.id,
                device_ip=device.ip,
                state=DeviceState.ONLINE,
                last_check=now,
                last_online=now,
                consecutive_failures=0
            )
        else:
            consecutive = (prev_record.consecutive_failures + 1) if prev_record else 1
            last_online = prev_record.last_online if prev_record else None
            
            record = DeviceHealthRecord(
                device_id=device.id,
                device_ip=device.ip,
                state=status.state,
                last_check=now,
                last_online=last_online,
                consecutive_failures=consecutive,
                error_message=self._get_error_from_status(status)
            )
        
        # Сохраняем
        self._health_records[device.id] = record
        
        return record
    
    def _get_error_from_status(self, status: DeviceStatus) -> Optional[str]:
        """Извлечь сообщение об ошибке из статуса."""
        for check in status.checks:
            if not check.success:
                return check.message
        return None
    
    async def check_all_devices(self) -> Dict[str, Any]:
        """
        Проверить все устройства.
        
        Returns:
            Сводка по результатам проверки
        """
        start_time = time.time()
        
        devices = self.registry.get_devices(enabled_only=True)
        
        logger.info(
            "monitor_check_start",
            total_devices=len(devices)
        )
        
        # Параллельная проверка
        tasks = [self.check_device(d) for d in devices]
        records = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Обрабатываем результаты
        online_count = 0
        offline_count = 0
        degraded_count = 0
        current_online_set: Set[str] = set()
        newly_offline: List[str] = []
        newly_online: List[str] = []
        
        for i, record in enumerate(records):
            if isinstance(record, Exception):
                logger.error(
                    "monitor_check_error",
                    device_id=devices[i].id,
                    error=str(record)
                )
                offline_count += 1
                continue
            
            if record.state == DeviceState.ONLINE:
                online_count += 1
                current_online_set.add(record.device_id)
            elif record.state == DeviceState.DEGRADED:
                degraded_count += 1
                current_online_set.add(record.device_id)  # Degraded = всё ещё "работает"
            else:
                offline_count += 1
        
        # Детектим изменения
        if self._previous_online_set:
            newly_offline = list(self._previous_online_set - current_online_set)
            newly_online = list(current_online_set - self._previous_online_set)
        
        self._previous_online_set = current_online_set
        self._last_check = datetime.now()
        
        # Генерируем алерты
        self._process_alerts(
            online_count=online_count,
            offline_count=offline_count,
            total_devices=len(devices),
            newly_offline=newly_offline,
            newly_online=newly_online
        )
        
        duration = time.time() - start_time
        
        summary = {
            "timestamp": self._last_check.isoformat(),
            "total_devices": len(devices),
            "online": online_count,
            "offline": offline_count,
            "degraded": degraded_count,
            "online_rate": online_count / max(len(devices), 1),
            "newly_offline": newly_offline,
            "newly_online": newly_online,
            "alerts_generated": len([a for a in self._alerts if a.timestamp >= self._last_check - timedelta(seconds=1)]),
            "duration_seconds": duration
        }
        
        logger.info("monitor_check_complete", **summary)
        
        return summary
    
    def _process_alerts(
        self,
        online_count: int,
        offline_count: int,
        total_devices: int,
        newly_offline: List[str],
        newly_online: List[str]
    ) -> None:
        """
        Обработать и сгенерировать алерты.
        
        Args:
            online_count: Количество онлайн устройств
            offline_count: Количество офлайн устройств
            total_devices: Всего устройств
            newly_offline: Только что упавшие
            newly_online: Только что восстановившиеся
        """
        now = datetime.now()
        
        # Алерт на восстановление
        for device_id in newly_online:
            alert = Alert(
                timestamp=now,
                level=AlertLevel.INFO,
                alert_type=AlertType.DEVICE_RECOVERED,
                message=f"Device {device_id} is back online",
                device_ids=[device_id]
            )
            self._alerts.append(alert)
            logger.info("alert_device_recovered", device_id=device_id)
        
        # Алерт на падение отдельных устройств
        for device_id in newly_offline:
            record = self._health_records.get(device_id)
            
            # Проверяем, нужен ли алерт
            if record and record.consecutive_failures >= self.config.consecutive_failures_alert:
                alert = Alert(
                    timestamp=now,
                    level=AlertLevel.WARNING,
                    alert_type=AlertType.DEVICE_DOWN,
                    message=f"Device {device_id} is offline ({record.consecutive_failures} consecutive failures)",
                    device_ids=[device_id],
                    details={
                        "consecutive_failures": record.consecutive_failures,
                        "last_online": record.last_online.isoformat() if record.last_online else None,
                        "error": record.error_message
                    }
                )
                self._alerts.append(alert)
                logger.warning("alert_device_down", **alert.to_dict())
        
        # Алерт на множественное падение (возможна проблема с сетью)
        if len(newly_offline) >= self.config.multi_device_alert_count:
            level = AlertLevel.CRITICAL
            alert_type = AlertType.MULTIPLE_DEVICES_DOWN
            
            if len(newly_offline) >= self.config.network_issue_threshold:
                level = AlertLevel.RED_ALERT
                alert_type = AlertType.NETWORK_ISSUE
            
            alert = Alert(
                timestamp=now,
                level=level,
                alert_type=alert_type,
                message=f"{len(newly_offline)} devices went offline simultaneously - possible network issue",
                device_ids=newly_offline,
                details={"count": len(newly_offline)}
            )
            self._alerts.append(alert)
            logger.error("alert_mass_failure", **alert.to_dict())
        
        # Алерт на низкий процент онлайн устройств
        online_rate = online_count / max(total_devices, 1)
        if online_rate < self.config.alert_threshold:
            alert = Alert(
                timestamp=now,
                level=AlertLevel.CRITICAL,
                alert_type=AlertType.THRESHOLD_BREACH,
                message=f"Online rate ({online_rate:.1%}) is below threshold ({self.config.alert_threshold:.1%})",
                details={
                    "online_count": online_count,
                    "total_devices": total_devices,
                    "online_rate": online_rate,
                    "threshold": self.config.alert_threshold
                }
            )
            self._alerts.append(alert)
            logger.error("alert_threshold_breach", **alert.to_dict())
    
    def get_device_health(self, device_id: str) -> Optional[DeviceHealthRecord]:
        """
        Получить запись о здоровье устройства.
        
        Args:
            device_id: ID устройства
            
        Returns:
            DeviceHealthRecord или None
        """
        return self._health_records.get(device_id)
    
    def get_all_health_records(self) -> List[DeviceHealthRecord]:
        """
        Получить все записи о здоровье.
        
        Returns:
            Список DeviceHealthRecord
        """
        return list(self._health_records.values())
    
    def get_offline_devices(self) -> List[DeviceHealthRecord]:
        """
        Получить офлайн устройства.
        
        Returns:
            Список DeviceHealthRecord
        """
        return [
            r for r in self._health_records.values()
            if r.state == DeviceState.OFFLINE
        ]
    
    def get_alerts(
        self,
        since: Optional[datetime] = None,
        level: Optional[AlertLevel] = None
    ) -> List[Alert]:
        """
        Получить алерты.
        
        Args:
            since: Начиная с даты
            level: Фильтр по уровню
            
        Returns:
            Список Alert
        """
        alerts = self._alerts
        
        if since:
            alerts = [a for a in alerts if a.timestamp >= since]
        
        if level:
            alerts = [a for a in alerts if a.level == level]
        
        return alerts
    
    def get_recent_alerts(self, hours: int = 24) -> List[Alert]:
        """
        Получить недавние алерты.
        
        Args:
            hours: Количество часов
            
        Returns:
            Список Alert
        """
        since = datetime.now() - timedelta(hours=hours)
        return self.get_alerts(since=since)
    
    def clear_old_alerts(self, days: int = 7) -> int:
        """
        Очистить старые алерты.
        
        Args:
            days: Количество дней
            
        Returns:
            Количество удалённых алертов
        """
        cutoff = datetime.now() - timedelta(days=days)
        original_count = len(self._alerts)
        self._alerts = [a for a in self._alerts if a.timestamp >= cutoff]
        return original_count - len(self._alerts)
    
    def get_summary(self) -> Dict[str, Any]:
        """
        Получить сводку мониторинга.
        
        Returns:
            Словарь со сводкой
        """
        records = self.get_all_health_records()
        
        online = [r for r in records if r.state == DeviceState.ONLINE]
        offline = [r for r in records if r.state == DeviceState.OFFLINE]
        degraded = [r for r in records if r.state == DeviceState.DEGRADED]
        
        recent_alerts = self.get_recent_alerts(hours=24)
        critical_alerts = [a for a in recent_alerts if a.level in [AlertLevel.CRITICAL, AlertLevel.RED_ALERT]]
        
        return {
            "last_check": self._last_check.isoformat() if self._last_check else None,
            "total_monitored": len(records),
            "online": len(online),
            "offline": len(offline),
            "degraded": len(degraded),
            "online_rate": len(online) / max(len(records), 1),
            "alerts_24h": len(recent_alerts),
            "critical_alerts_24h": len(critical_alerts),
            "offline_devices": [r.device_id for r in offline],
            "devices_with_issues": [
                r.device_id for r in records
                if r.consecutive_failures > 0
            ]
        }
    
    async def start_monitoring_loop(
        self,
        interval_override: Optional[int] = None
    ) -> None:
        """
        Запустить цикл мониторинга.
        
        Args:
            interval_override: Переопределить интервал (секунды)
        """
        self._running = True
        interval = interval_override or self.config.status_check_interval_sec
        
        logger.info(
            "monitor_loop_start",
            interval_sec=interval
        )
        
        while self._running:
            try:
                await self.check_all_devices()
            except Exception as e:
                logger.error("monitor_loop_error", error=str(e))
            
            await asyncio.sleep(interval)
    
    def stop_monitoring_loop(self) -> None:
        """Остановить цикл мониторинга."""
        self._running = False
        logger.info("monitor_loop_stopped")


# Global instance
_monitor_service: Optional[MonitorService] = None


def get_monitor_service(config_path: str = "config.json") -> MonitorService:
    """
    Получить глобальный экземпляр сервиса.
    
    Args:
        config_path: Путь к конфигурации
        
    Returns:
        MonitorService
    """
    global _monitor_service
    if _monitor_service is None:
        _monitor_service = MonitorService.from_config(config_path)
    return _monitor_service


# Пример использования:
if __name__ == "__main__":
    import asyncio
    
    async def main():
        # Создаём сервис
        monitor = MonitorService.from_config("config.json")
        
        print("=== Monitor Service Demo ===\n")
        
        # Проверяем все устройства
        print("Checking all devices...")
        summary = await monitor.check_all_devices()
        
        print(f"\nResults:")
        print(f"  Total: {summary['total_devices']}")
        print(f"  Online: {summary['online']}")
        print(f"  Offline: {summary['offline']}")
        print(f"  Online rate: {summary['online_rate']:.1%}")
        
        # Офлайн устройства
        offline = monitor.get_offline_devices()
        if offline:
            print(f"\nOffline devices:")
            for record in offline:
                print(f"  - {record.device_id}: {record.error_message}")
        
        # Алерты
        alerts = monitor.get_recent_alerts(hours=1)
        if alerts:
            print(f"\nRecent alerts ({len(alerts)}):")
            for alert in alerts:
                emoji = {
                    AlertLevel.INFO: "ℹ️",
                    AlertLevel.WARNING: "⚠️",
                    AlertLevel.CRITICAL: "🚨",
                    AlertLevel.RED_ALERT: "🔴"
                }.get(alert.level, "❓")
                print(f"  {emoji} {alert.message}")
        
        # Сводка
        print("\n=== Summary ===")
        summary = monitor.get_summary()
        print(f"Online rate: {summary['online_rate']:.1%}")
        print(f"Alerts (24h): {summary['alerts_24h']}")
        print(f"Critical alerts: {summary['critical_alerts_24h']}")
    
    asyncio.run(main())
