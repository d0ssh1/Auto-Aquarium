"""
Base protocol adapter for device communication.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
from enum import Enum


class PowerState(Enum):
    """Device power state."""
    ON = "on"
    OFF = "off"
    UNKNOWN = "unknown"


@dataclass
class DeviceResult:
    """Result of a device operation."""
    success: bool
    message: str
    power_state: PowerState = PowerState.UNKNOWN
    duration_ms: int = 0
    response: Optional[str] = None
    error: Optional[str] = None


class BaseProtocol(ABC):
    """Abstract base class for device protocols."""
    
    def __init__(self, ip: str, port: int, timeout: int = 10):
        self.ip = ip
        self.port = port
        self.timeout = timeout
    
    @abstractmethod
    async def turn_on(self) -> DeviceResult:
        """Turn on the device."""
        pass
    
    @abstractmethod
    async def turn_off(self) -> DeviceResult:
        """Turn off the device."""
        pass
    
    @abstractmethod
    async def get_status(self) -> DeviceResult:
        """Get current power status."""
        pass
    
    async def check_reachable(self) -> bool:
        """Check if device is reachable via TCP."""
        import asyncio
        import socket
        
        try:
            loop = asyncio.get_event_loop()
            # Create TCP connection with timeout
            future = loop.run_in_executor(
                None,
                lambda: socket.create_connection((self.ip, self.port), timeout=self.timeout)
            )
            conn = await asyncio.wait_for(future, timeout=self.timeout)
            conn.close()
            return True
        except Exception:
            return False
