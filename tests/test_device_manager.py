"""
Tests for Device Manager.
"""

import asyncio
import pytest
from unittest.mock import Mock, AsyncMock, patch
import sys
from pathlib import Path

# Add app to path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))


class MockDevice:
    """Mock device for testing."""
    def __init__(self, id, name, ip, port, device_type, group_id, enabled=True):
        self.id = id
        self.name = name
        self.ip = ip
        self.port = port
        self.device_type = device_type
        self.group_id = group_id
        self.enabled = enabled


class MockRegistry:
    """Mock device registry."""
    def __init__(self, devices):
        self._devices = devices
    
    def get_devices(self, enabled_only=False):
        if enabled_only:
            return [d for d in self._devices if d.enabled]
        return self._devices
    
    def get_device(self, device_id):
        for d in self._devices:
            if d.id == device_id:
                return d
        return None
    
    def get_by_group(self, group_id, enabled_only=False):
        devices = [d for d in self._devices if d.group_id == group_id]
        if enabled_only:
            devices = [d for d in devices if d.enabled]
        return devices


class TestDeviceManagerBasics:
    """Basic tests for DeviceManager."""
    
    def test_execution_report_summary(self):
        """Test ExecutionReport text summary."""
        from services.device_manager import ExecutionReport
        from datetime import datetime
        
        report = ExecutionReport(
            timestamp=datetime.now(),
            action="TURN_ON",
            total_devices=10,
            successful=9,
            failed=1,
            devices_with_errors=["device_1"],
            retry_count=3,
            duration_seconds=5.5,
            status="PARTIAL"
        )
        
        summary = report.to_summary()
        
        assert "TURN_ON" in summary
        assert "10" in summary
        assert "9" in summary
        assert "90.0%" in summary
        assert "device_1" in summary
    
    def test_execution_report_to_dict(self):
        """Test ExecutionReport dict conversion."""
        from services.device_manager import ExecutionReport
        from datetime import datetime
        
        report = ExecutionReport(
            timestamp=datetime.now(),
            action="TURN_OFF",
            total_devices=5,
            successful=5,
            failed=0,
            status="SUCCESS"
        )
        
        data = report.to_dict()
        
        assert data["action"] == "TURN_OFF"
        assert data["total_devices"] == 5
        assert data["successful"] == 5
        assert data["success_rate"] == 1.0


class TestDeviceManagerOperations:
    """Test device operations."""
    
    @pytest.fixture
    def mock_devices(self):
        """Create mock devices."""
        return [
            MockDevice("optoma_1", "Optoma 1", "192.168.2.64", 23, "optoma_telnet", "projectors"),
            MockDevice("optoma_2", "Optoma 2", "192.168.2.65", 23, "optoma_telnet", "projectors"),
            MockDevice("barco_1", "Barco 1", "192.168.1.95", 9090, "barco_jsonrpc", "projectors"),
            MockDevice("expo_1", "Expo PC 1", "192.168.4.50", None, "exposition_pc", "expositions"),
        ]
    
    @pytest.fixture
    def mock_registry(self, mock_devices):
        """Create mock registry."""
        return MockRegistry(mock_devices)
    
    @pytest.mark.asyncio
    async def test_turn_on_device_not_found(self, mock_registry):
        """Test turning on non-existent device."""
        from services.device_manager import DeviceManager
        
        manager = DeviceManager(registry=mock_registry)
        
        result = await manager.turn_on_device("nonexistent")
        
        assert result.success is False
        assert "not found" in result.error.lower()
    
    @pytest.mark.asyncio
    async def test_build_report(self):
        """Test report building from results."""
        from services.device_manager import DeviceManager, ActionType, DeviceResult
        
        manager = DeviceManager()
        
        results = [
            DeviceResult("d1", "Device 1", "1.1.1.1", "optoma", True, 1, 100),
            DeviceResult("d2", "Device 2", "1.1.1.2", "optoma", True, 2, 200),
            DeviceResult("d3", "Device 3", "1.1.1.3", "optoma", False, 3, 300, error="Timeout"),
        ]
        
        report = manager._build_report(ActionType.TURN_ON, results, 0.6)
        
        assert report.total_devices == 3
        assert report.successful == 2
        assert report.failed == 1
        assert report.status == "FAILED"  # 66% success rate is below 80% threshold
        assert "d3" in report.devices_with_errors


class TestRetryPolicy:
    """Test retry policy configuration."""
    
    def test_retry_policy_defaults(self):
        """Test default retry policy values."""
        from services.device_manager import RetryPolicy
        
        policy = RetryPolicy()
        
        assert policy.max_attempts == 3
        assert policy.base_interval_sec == 30
        assert policy.backoff_multiplier == 2.0
    
    def test_retry_policy_custom(self):
        """Test custom retry policy."""
        from services.device_manager import RetryPolicy
        
        policy = RetryPolicy(
            max_attempts=5,
            base_interval_sec=10,
            backoff_multiplier=1.5
        )
        
        assert policy.max_attempts == 5
        assert policy.base_interval_sec == 10
        assert policy.backoff_multiplier == 1.5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
