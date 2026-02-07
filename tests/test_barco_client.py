"""
Tests for Barco Client (JSON-RPC).
"""

import asyncio
import json
import pytest
from unittest.mock import Mock, patch
import sys
from pathlib import Path

# Add app to path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from protocols.barco_client import BarcoClient, BarcoResult


class TestBarcoClient:
    """Unit tests for BarcoClient."""
    
    @pytest.fixture
    def client(self):
        """Create a BarcoClient instance."""
        return BarcoClient(
            timeout=2,
            max_retries=2,
            base_delay=1,
            max_delay=5
        )
    
    def test_client_initialization(self, client):
        """Test client initializes with correct defaults."""
        assert client.timeout == 2
        assert client.max_retries == 2
        assert client.base_delay == 1
        assert client.max_delay == 5
    
    def test_request_building(self, client):
        """Test JSON-RPC request building."""
        import json
        request_str = client._build_request("system.poweron")
        request = json.loads(request_str.strip())
        
        assert request["jsonrpc"] == "2.0"
        assert request["method"] == "system.poweron"
        assert "id" in request
    
    def test_request_with_params(self, client):
        """Test JSON-RPC request with parameters."""
        import json
        request_str = client._build_request("custom.method", {"key": "value"})
        request = json.loads(request_str.strip())
        
        assert request["params"] == {"key": "value"}
    
    def test_result_serialization(self):
        """Test BarcoResult serialization."""
        result = BarcoResult(
            success=True,
            message="Command executed",
            method="system.poweron",
            device_ip="192.168.1.95",
            device_port=9090,
            attempt_count=1,
            total_duration_ms=200,
            response_data={"result": "ok"}
        )
        
        data = result.to_dict()
        
        assert data["success"] is True
        assert data["method"] == "system.poweron"
        assert data["device_ip"] == "192.168.1.95"
        assert data["response_data"] == {"result": "ok"}
    
    def test_result_json(self):
        """Test BarcoResult JSON output."""
        result = BarcoResult(
            success=True,
            message="OK",
            method="system.poweron",
            device_ip="192.168.1.95",
            device_port=9090,
            attempt_count=1,
            total_duration_ms=100
        )
        
        json_str = result.to_json()
        parsed = json.loads(json_str)
        
        assert parsed["success"] is True
        assert parsed["method"] == "system.poweron"


class TestBarcoClientWithMocks:
    """Tests with mocked socket connections."""
    
    @pytest.fixture
    def mock_socket(self):
        """Create a mock socket."""
        response = json.dumps({
            "jsonrpc": "2.0",
            "result": {"status": "ok"},
            "id": 1
        }).encode()
        
        mock = Mock()
        mock.settimeout = Mock()
        mock.connect = Mock()
        mock.sendall = Mock()
        mock.recv = Mock(return_value=response)
        mock.close = Mock()
        return mock
    
    @pytest.fixture
    def client_with_mock(self, mock_socket):
        """Create client with mocked socket factory."""
        client = BarcoClient(
            timeout=1,
            max_retries=1,
            base_delay=0.1,
            socket_factory=lambda: mock_socket
        )
        return client, mock_socket
    
    @pytest.mark.asyncio
    async def test_successful_command(self, client_with_mock):
        """Test successful JSON-RPC command."""
        client, mock_socket = client_with_mock
        
        result = await client.send_command(
            ip="192.168.1.95",
            method="system.poweron"
        )
        
        assert result.success is True
        assert result.attempt_count == 1
        mock_socket.connect.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_jsonrpc_error_response(self):
        """Test handling of JSON-RPC error response."""
        error_response = json.dumps({
            "jsonrpc": "2.0",
            "error": {"code": -32600, "message": "Invalid Request"},
            "id": 1
        }).encode()
        
        def error_socket():
            mock = Mock()
            mock.settimeout = Mock()
            mock.connect = Mock()
            mock.sendall = Mock()
            mock.recv = Mock(return_value=error_response)
            mock.close = Mock()
            return mock
        
        client = BarcoClient(
            timeout=1,
            max_retries=1,
            base_delay=0.1,
            socket_factory=error_socket
        )
        
        result = await client.send_command(
            ip="192.168.1.95",
            method="invalid.method"
        )
        
        assert result.success is False
        assert result.error_code == -32600
    
    @pytest.mark.asyncio
    async def test_connection_refused(self):
        """Test connection refused handling."""
        def failing_socket():
            mock = Mock()
            mock.settimeout = Mock()
            mock.connect = Mock(side_effect=ConnectionRefusedError())
            mock.close = Mock()
            return mock
        
        client = BarcoClient(
            timeout=1,
            max_retries=1,
            base_delay=0.1,
            socket_factory=failing_socket
        )
        
        result = await client.send_command(
            ip="192.168.1.95",
            method="system.poweron"
        )
        
        assert result.success is False
        assert result.error_type == "CONNECTION_REFUSED"


class TestBarcoClientHelpers:
    """Test convenience methods."""
    
    @pytest.fixture
    def client(self):
        return BarcoClient()
    
    @pytest.mark.asyncio
    async def test_power_on(self, client):
        """Test power_on convenience method."""
        async def mock_send(*args, **kwargs):
            return BarcoResult(
                success=True,
                message="OK",
                method="system.poweron",
                device_ip="192.168.1.95",
                device_port=9090,
                attempt_count=1,
                total_duration_ms=100
            )
        
        client.send_command = mock_send
        
        result = await client.power_on("192.168.1.95")
        
        assert result.success is True
    
    @pytest.mark.asyncio
    async def test_power_off(self, client):
        """Test power_off convenience method."""
        async def mock_send(*args, **kwargs):
            return BarcoResult(
                success=True,
                message="OK",
                method="system.poweroff",
                device_ip="192.168.1.95",
                device_port=9090,
                attempt_count=1,
                total_duration_ms=100
            )
        
        client.send_command = mock_send
        
        result = await client.power_off("192.168.1.95")
        
        assert result.success is True
    
    @pytest.mark.asyncio
    async def test_get_power_state(self, client):
        """Test get_power_state convenience method."""
        async def mock_send(*args, **kwargs):
            return BarcoResult(
                success=True,
                message="OK",
                method="system.powerstate.get",
                device_ip="192.168.1.95",
                device_port=9090,
                attempt_count=1,
                total_duration_ms=100,
                response_data={"state": 1}
            )
        
        client.send_command = mock_send
        
        result = await client.get_power_state("192.168.1.95")
        
        assert result.success is True
        assert result.response_data["state"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
