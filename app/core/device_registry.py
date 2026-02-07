"""
Device Registry — Реестр устройств и загрузка конфигурации.

Модуль для управления реестром устройств:
- Загрузка из config.json
- Валидация конфигурации через Pydantic
- Доступ к устройствам по ID, группе, типу
- Hot reload конфигурации

Использование:
    registry = DeviceRegistry.from_config("config.json")
    device = registry.get_device("optoma_2.64")
    projectors = registry.get_by_type(DeviceType.OPTOMA_TELNET)
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, List, Dict, Any, Iterator

import structlog
from pydantic import BaseModel, Field, validator

logger = structlog.get_logger()


class DeviceType(str, Enum):
    """Типы устройств."""
    OPTOMA_TELNET = "optoma_telnet"
    BARCO_JSONRPC = "barco_jsonrpc"
    CUBES_CUSTOM = "cubes_custom"
    EXPOSITION_PC = "exposition_pc"
    GENERIC_TCP = "generic_tcp"


class DeviceProtocol(str, Enum):
    """Протоколы связи."""
    TELNET = "telnet"
    JSONRPC = "jsonrpc"
    CUSTOM_TCP = "custom_tcp"
    HTTP = "http"
    PING_ONLY = "ping_only"


class Device(BaseModel):
    """
    Модель устройства.
    
    Attributes:
        id: Уникальный идентификатор
        name: Человекочитаемое имя
        ip: IP адрес
        port: Порт подключения
        device_type: Тип устройства
        group: Группа устройств
        enabled: Включено ли устройство
        mac: MAC адрес (для WoL)
        timeout_sec: Таймаут операций
        reason_disabled: Причина отключения
    """
    id: str
    name: str
    ip: str
    port: Optional[int] = None
    device_type: DeviceType = Field(alias="type")
    group: str = "default"
    enabled: bool = True
    mac: Optional[str] = None
    timeout_sec: int = 10
    reason_disabled: Optional[str] = None
    
    class Config:
        use_enum_values = True
        populate_by_name = True
    
    @validator("ip")
    def validate_ip(cls, v):
        """Валидация IP адреса."""
        parts = v.split(".")
        if len(parts) != 4:
            raise ValueError(f"Invalid IP address: {v}")
        for part in parts:
            if not part.isdigit() or not 0 <= int(part) <= 255:
                raise ValueError(f"Invalid IP address: {v}")
        return v
    
    @validator("port")
    def validate_port(cls, v):
        """Валидация порта."""
        if v is not None and (v < 1 or v > 65535):
            raise ValueError(f"Port must be 1-65535, got: {v}")
        return v
    
    @validator("mac")
    def validate_mac(cls, v):
        """Валидация и нормализация MAC адреса."""
        if v is None:
            return None
        # Нормализуем в формат XX:XX:XX:XX:XX:XX
        cleaned = v.replace("-", ":").replace(".", ":").upper()
        parts = cleaned.split(":")
        if len(parts) != 6:
            raise ValueError(f"Invalid MAC address: {v}")
        return cleaned
    
    @property
    def protocol(self) -> DeviceProtocol:
        """Получить протокол по типу устройства."""
        mapping = {
            DeviceType.OPTOMA_TELNET: DeviceProtocol.TELNET,
            DeviceType.BARCO_JSONRPC: DeviceProtocol.JSONRPC,
            DeviceType.CUBES_CUSTOM: DeviceProtocol.CUSTOM_TCP,
            DeviceType.EXPOSITION_PC: DeviceProtocol.PING_ONLY,
            DeviceType.GENERIC_TCP: DeviceProtocol.CUSTOM_TCP,
        }
        return mapping.get(self.device_type, DeviceProtocol.PING_ONLY)
    
    @property
    def default_port(self) -> int:
        """Получить порт по умолчанию для типа."""
        defaults = {
            DeviceType.OPTOMA_TELNET: 23,
            DeviceType.BARCO_JSONRPC: 9090,
            DeviceType.CUBES_CUSTOM: 7992,
            DeviceType.EXPOSITION_PC: None,
        }
        return self.port or defaults.get(self.device_type)
    
    def to_dict(self) -> Dict[str, Any]:
        """Конвертировать в словарь."""
        return {
            "id": self.id,
            "name": self.name,
            "ip": self.ip,
            "port": self.port,
            "type": self.device_type,
            "group": self.group,
            "enabled": self.enabled,
            "mac": self.mac,
            "timeout_sec": self.timeout_sec,
            "protocol": self.protocol.value
        }


class DeviceGroup(BaseModel):
    """
    Группа устройств.
    
    Attributes:
        id: Идентификатор группы
        name: Человекочитаемое имя
        priority: Приоритет выполнения (1 = первый)
        parallel: Выполнять операции параллельно
    """
    id: str
    name: str
    priority: int = 1
    parallel: bool = True
    
    class Config:
        use_enum_values = True


class RegistryConfig(BaseModel):
    """
    Конфигурация реестра.
    
    Attributes:
        devices: Список устройств
        groups: Список групп
    """
    devices: List[Device] = Field(default_factory=list)
    groups: List[DeviceGroup] = Field(default_factory=list)


class DeviceRegistry:
    """
    Реестр устройств.
    
    Централизованное хранилище информации о всех устройствах.
    Поддерживает различные способы поиска и фильтрации.
    
    Attributes:
        config_path: Путь к файлу конфигурации
        devices: Словарь устройств (id -> Device)
        groups: Словарь групп (id -> DeviceGroup)
    """
    
    def __init__(
        self,
        devices: Optional[List[Device]] = None,
        groups: Optional[List[DeviceGroup]] = None,
        config_path: Optional[str] = None
    ):
        """
        Инициализация реестра.
        
        Args:
            devices: Список устройств
            groups: Список групп
            config_path: Путь к конфигурации
        """
        self._devices: Dict[str, Device] = {}
        self._groups: Dict[str, DeviceGroup] = {}
        self._config_path = config_path
        self._loaded_at: Optional[datetime] = None
        
        if devices:
            for device in devices:
                self._devices[device.id] = device
        
        if groups:
            for group in groups:
                self._groups[group.id] = group
    
    @classmethod
    def from_config(cls, config_path: str) -> "DeviceRegistry":
        """
        Создать реестр из файла конфигурации.
        
        Args:
            config_path: Путь к config.json
            
        Returns:
            DeviceRegistry
        """
        path = Path(config_path)
        
        if not path.exists():
            logger.warning("config_not_found", path=config_path)
            return cls(config_path=config_path)
        
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # Парсим устройства
            devices = []
            for device_data in data.get("devices", []):
                try:
                    device = Device(**device_data)
                    devices.append(device)
                except Exception as e:
                    logger.error(
                        "device_parse_error",
                        device_id=device_data.get("id", "unknown"),
                        error=str(e)
                    )
            
            # Парсим группы
            groups = []
            for group_data in data.get("groups", []):
                try:
                    group = DeviceGroup(**group_data)
                    groups.append(group)
                except Exception as e:
                    logger.error(
                        "group_parse_error",
                        group_id=group_data.get("id", "unknown"),
                        error=str(e)
                    )
            
            registry = cls(
                devices=devices,
                groups=groups,
                config_path=config_path
            )
            registry._loaded_at = datetime.now()
            
            logger.info(
                "registry_loaded",
                devices=len(devices),
                groups=len(groups),
                path=config_path
            )
            
            return registry
            
        except json.JSONDecodeError as e:
            logger.error("config_json_error", path=config_path, error=str(e))
            return cls(config_path=config_path)
            
        except Exception as e:
            logger.error("config_load_error", path=config_path, error=str(e))
            return cls(config_path=config_path)
    
    def reload(self) -> bool:
        """
        Перезагрузить конфигурацию из файла.
        
        Returns:
            True если успешно
        """
        if not self._config_path:
            logger.warning("cannot_reload", reason="no_config_path")
            return False
        
        new_registry = DeviceRegistry.from_config(self._config_path)
        
        self._devices = new_registry._devices
        self._groups = new_registry._groups
        self._loaded_at = datetime.now()
        
        logger.info(
            "registry_reloaded",
            devices=len(self._devices),
            groups=len(self._groups)
        )
        
        return True
    
    # === Доступ к устройствам ===
    
    def get_device(self, device_id: str) -> Optional[Device]:
        """
        Получить устройство по ID.
        
        Args:
            device_id: ID устройства
            
        Returns:
            Device или None
        """
        return self._devices.get(device_id)
    
    def get_devices(self, enabled_only: bool = False) -> List[Device]:
        """
        Получить все устройства.
        
        Args:
            enabled_only: Только включённые
            
        Returns:
            Список устройств
        """
        devices = list(self._devices.values())
        if enabled_only:
            devices = [d for d in devices if d.enabled]
        return devices
    
    def get_by_type(
        self,
        device_type: DeviceType,
        enabled_only: bool = True
    ) -> List[Device]:
        """
        Получить устройства по типу.
        
        Args:
            device_type: Тип устройства
            enabled_only: Только включённые
            
        Returns:
            Список устройств
        """
        devices = [
            d for d in self._devices.values()
            if d.device_type == device_type
        ]
        if enabled_only:
            devices = [d for d in devices if d.enabled]
        return devices
    
    def get_by_group(
        self,
        group_id: str,
        enabled_only: bool = True
    ) -> List[Device]:
        """
        Получить устройства по группе.
        
        Args:
            group_id: ID группы
            enabled_only: Только включённые
            
        Returns:
            Список устройств
        """
        devices = [
            d for d in self._devices.values()
            if d.group == group_id
        ]
        if enabled_only:
            devices = [d for d in devices if d.enabled]
        return devices
    
    def get_by_ip(self, ip: str) -> Optional[Device]:
        """
        Получить устройство по IP адресу.
        
        Args:
            ip: IP адрес
            
        Returns:
            Device или None
        """
        for device in self._devices.values():
            if device.ip == ip:
                return device
        return None
    
    # === Доступ к группам ===
    
    def get_group(self, group_id: str) -> Optional[DeviceGroup]:
        """
        Получить группу по ID.
        
        Args:
            group_id: ID группы
            
        Returns:
            DeviceGroup или None
        """
        return self._groups.get(group_id)
    
    def get_groups(self) -> List[DeviceGroup]:
        """
        Получить все группы.
        
        Returns:
            Список групп
        """
        return list(self._groups.values())
    
    def get_groups_sorted(self) -> List[DeviceGroup]:
        """
        Получить группы отсортированные по приоритету.
        
        Returns:
            Список групп (priority ascending)
        """
        return sorted(self._groups.values(), key=lambda g: g.priority)
    
    # === Статистика ===
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Получить статистику реестра.
        
        Returns:
            Словарь со статистикой
        """
        enabled = [d for d in self._devices.values() if d.enabled]
        disabled = [d for d in self._devices.values() if not d.enabled]
        
        by_type = {}
        for device in self._devices.values():
            type_name = device.device_type
            by_type[type_name] = by_type.get(type_name, 0) + 1
        
        by_group = {}
        for device in self._devices.values():
            by_group[device.group] = by_group.get(device.group, 0) + 1
        
        return {
            "total_devices": len(self._devices),
            "enabled_devices": len(enabled),
            "disabled_devices": len(disabled),
            "total_groups": len(self._groups),
            "by_type": by_type,
            "by_group": by_group,
            "loaded_at": self._loaded_at.isoformat() if self._loaded_at else None,
            "config_path": self._config_path
        }
    
    # === Итерация ===
    
    def __iter__(self) -> Iterator[Device]:
        """Итерация по устройствам."""
        return iter(self._devices.values())
    
    def __len__(self) -> int:
        """Количество устройств."""
        return len(self._devices)
    
    def __contains__(self, device_id: str) -> bool:
        """Проверка наличия устройства."""
        return device_id in self._devices


# Global registry instance
_registry: Optional[DeviceRegistry] = None


def get_registry(config_path: str = "config.json") -> DeviceRegistry:
    """
    Получить глобальный экземпляр реестра.
    
    Args:
        config_path: Путь к конфигурации
        
    Returns:
        DeviceRegistry
    """
    global _registry
    if _registry is None:
        _registry = DeviceRegistry.from_config(config_path)
    return _registry


def reload_registry() -> DeviceRegistry:
    """
    Перезагрузить глобальный реестр.
    
    Returns:
        Обновлённый DeviceRegistry
    """
    global _registry
    if _registry:
        _registry.reload()
    return _registry


# Пример использования:
if __name__ == "__main__":
    # Создаём реестр из конфигурации
    registry = DeviceRegistry.from_config("config.json")
    
    # Статистика
    print("=== Статистика реестра ===")
    stats = registry.get_stats()
    print(f"Всего устройств: {stats['total_devices']}")
    print(f"Включённых: {stats['enabled_devices']}")
    print(f"Групп: {stats['total_groups']}")
    
    print("\nПо типам:")
    for type_name, count in stats["by_type"].items():
        print(f"  {type_name}: {count}")
    
    print("\nПо группам:")
    for group_name, count in stats["by_group"].items():
        print(f"  {group_name}: {count}")
    
    # Получаем устройства
    print("\n=== Projectors (Optoma) ===")
    optomas = registry.get_by_type(DeviceType.OPTOMA_TELNET)
    for device in optomas:
        print(f"  {device.id}: {device.ip}:{device.default_port}")
    
    print("\n=== Projectors (Barco) ===")
    barcos = registry.get_by_type(DeviceType.BARCO_JSONRPC)
    for device in barcos:
        print(f"  {device.id}: {device.ip}:{device.default_port}")
    
    # Получаем группы по приоритету
    print("\n=== Группы (по приоритету) ===")
    for group in registry.get_groups_sorted():
        devices = registry.get_by_group(group.id)
        print(f"  [{group.priority}] {group.name}: {len(devices)} устройств")
    
    # Поиск по IP
    print("\n=== Поиск по IP ===")
    device = registry.get_by_ip("192.168.2.64")
    if device:
        print(f"  Найдено: {device.name} ({device.device_type})")
