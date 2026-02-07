"""
JSON-RPC client for Barco projectors.
Protocol: JSON-RPC 2.0 over TCP (port 9090)
Commands: system.poweron, system.poweroff
"""

import asyncio
import json
import time
from typing import Optional

from .base import BaseProtocol, DeviceResult, PowerState


class BarcoJsonRpcClient(BaseProtocol):
    """
    Barco projector control via JSON-RPC.
    
    Uses JSON-RPC 2.0 format over raw TCP socket.
    """
    
    def __init__(self, ip: str, port: int = 9090, timeout: int = 10):
        super().__init__(ip, port, timeout)
        self._request_id = 0
    
    def _build_request(self, method: str, params: dict = None) -> str:
        """Build a JSON-RPC request."""
        self._request_id += 1
        request = {
            "jsonrpc": "2.0",
            "method": method,
            "id": self._request_id
        }
        if params:
            request["params"] = params
        return json.dumps(request) + "\n"
    
    async def _send_command(self, method: str, params: dict = None) -> DeviceResult:
        """Send a JSON-RPC command and get response."""
        start_time = time.time()
        request = self._build_request(method, params)
        
        try:
            # Connect to device
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.ip, self.port),
                timeout=self.timeout
            )
            
            # Send request
            writer.write(request.encode('utf-8'))
            await writer.drain()
            
            # Read response
            try:
                response_data = await asyncio.wait_for(
                    reader.readline(),
                    timeout=5
                )
                response_text = response_data.decode('utf-8').strip()
                
                if response_text:
                    response_json = json.loads(response_text)
                    
                    # Check for JSON-RPC error
                    if "error" in response_json:
                        error = response_json["error"]
                        raise Exception(f"JSON-RPC error: {error.get('message', str(error))}")
                    
                    result_data = response_json.get("result", {})
                else:
                    result_data = {}
                    
            except asyncio.TimeoutError:
                # Some commands may not return response
                response_text = ""
                result_data = {}
            
            # Close connection
            writer.close()
            await writer.wait_closed()
            
            duration_ms = int((time.time() - start_time) * 1000)
            
            return DeviceResult(
                success=True,
                message="Command executed successfully",
                duration_ms=duration_ms,
                response=response_text
            )
            
        except asyncio.TimeoutError:
            duration_ms = int((time.time() - start_time) * 1000)
            return DeviceResult(
                success=False,
                message="Connection timeout",
                duration_ms=duration_ms,
                error=f"Timeout connecting to {self.ip}:{self.port}"
            )
            
        except ConnectionRefusedError:
            duration_ms = int((time.time() - start_time) * 1000)
            return DeviceResult(
                success=False,
                message="Connection refused",
                duration_ms=duration_ms,
                error=f"Connection refused by {self.ip}:{self.port}"
            )
            
        except json.JSONDecodeError as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return DeviceResult(
                success=False,
                message="Invalid JSON response",
                duration_ms=duration_ms,
                error=str(e)
            )
            
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return DeviceResult(
                success=False,
                message="Command failed",
                duration_ms=duration_ms,
                error=str(e)
            )
    
    async def turn_on(self) -> DeviceResult:
        """Turn on the Barco projector."""
        result = await self._send_command("system.poweron")
        if result.success:
            result.power_state = PowerState.ON
            result.message = "Projector powering on"
        return result
    
    async def turn_off(self) -> DeviceResult:
        """Turn off the Barco projector."""
        result = await self._send_command("system.poweroff")
        if result.success:
            result.power_state = PowerState.OFF
            result.message = "Projector powering off"
        return result
    
    async def get_status(self) -> DeviceResult:
        """Get Barco projector power status."""
        # Try to get power state via JSON-RPC
        result = await self._send_command("system.powerstate.get")
        
        if result.success and result.response:
            try:
                response = json.loads(result.response)
                state = response.get("result", {}).get("state", "unknown")
                
                if state in ["on", "ON", "1"]:
                    result.power_state = PowerState.ON
                elif state in ["off", "OFF", "0", "standby"]:
                    result.power_state = PowerState.OFF
                else:
                    result.power_state = PowerState.UNKNOWN
                    
            except (json.JSONDecodeError, KeyError):
                result.power_state = PowerState.UNKNOWN
        
        return result
