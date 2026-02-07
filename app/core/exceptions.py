"""
Custom exceptions for Ocean Control System.
"""


class OceanControlError(Exception):
    """Base exception for all Ocean Control errors."""
    pass


class DeviceError(OceanControlError):
    """Error related to device operations."""
    def __init__(self, device_id: str, message: str):
        self.device_id = device_id
        self.message = message
        super().__init__(f"Device {device_id}: {message}")


class ConnectionError(DeviceError):
    """Network connection error."""
    pass


class ProtocolError(DeviceError):
    """Protocol-specific error."""
    pass


class TimeoutError(DeviceError):
    """Operation timeout error."""
    pass


class ConfigurationError(OceanControlError):
    """Configuration error."""
    pass


class SchedulerError(OceanControlError):
    """Scheduler-related error."""
    pass
