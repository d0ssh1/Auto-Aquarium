"""
Configuration management using Pydantic Settings.
"""

import json
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class ScheduleConfig(BaseModel):
    """Schedule configuration."""
    on_time: str = "09:00"
    off_time: str = "20:00"
    timezone: str = "Asia/Vladivostok"
    enabled: bool = True


class RetryConfig(BaseModel):
    """Retry configuration."""
    max_attempts: int = 3
    interval_seconds: int = 30
    timeout_seconds: int = 10


class DeviceGroup(BaseModel):
    """Device group configuration."""
    id: str
    name: str
    priority: int = 1
    parallel: bool = True


class DeviceConfig(BaseModel):
    """Individual device configuration."""
    id: str
    name: str
    group: str
    type: str  # optoma_telnet, barco_jsonrpc, cubes_custom, exposition_pc
    ip: str
    port: Optional[int] = None
    mac: Optional[str] = None
    enabled: bool = True
    reason_disabled: Optional[str] = None


class ZabbixConfig(BaseModel):
    """Zabbix integration configuration."""
    enabled: bool = False
    url: str = "http://192.168.2.240/api_jsonrpc.php"
    api_token: str = ""


class LoggingConfig(BaseModel):
    """Logging configuration."""
    level: str = "INFO"
    json_file: str = "logs/actions.jsonl"
    retention_days: int = 30


class AppConfig(BaseModel):
    """Complete application configuration."""
    schedule: ScheduleConfig = ScheduleConfig()
    retry: RetryConfig = RetryConfig()
    groups: list[DeviceGroup] = []
    devices: list[DeviceConfig] = []
    zabbix: ZabbixConfig = ZabbixConfig()
    logging: LoggingConfig = LoggingConfig()


class Settings(BaseSettings):
    """Application settings."""
    app_name: str = "Ocean Control System"
    debug: bool = False
    database_url: str = "sqlite+aiosqlite:///data/ocean.db"
    config_path: str = "config.json"
    
    class Config:
        env_prefix = "OCEAN_"


# Global settings instance
settings = Settings()


def load_config() -> AppConfig:
    """Load configuration from JSON file."""
    config_path = Path(settings.config_path)
    
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return AppConfig(**data)
    
    # Return default config if file doesn't exist
    return AppConfig()


def save_config(config: AppConfig) -> None:
    """Save configuration to JSON file."""
    config_path = Path(settings.config_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config.model_dump(), f, indent=2, ensure_ascii=False)


# Global config instance (loaded on first access)
_config: Optional[AppConfig] = None


def get_config() -> AppConfig:
    """Get the current configuration."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reload_config() -> AppConfig:
    """Reload configuration from file."""
    global _config
    _config = load_config()
    return _config
