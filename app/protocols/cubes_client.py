"""
Custom protocol client for Cubes (Medialon) video wall system.
Protocol: Custom text commands over TCP (port 7992)
Commands: SET(0;Power;1), SET(0;Power;0), get(0;Power)
"""

import asyncio
import time
from typing import Optional

from .base import BaseProtocol, DeviceResult, PowerState


class CubesClient(BaseProtocol):
    """
    Cubes (Medialon) video wall control via custom TCP protocol.
    
    Uses simple text command format:
    - SET(channel;property;value)
    - get(channel;property)
    """
    
    # Cubes commands
    CMD_POWER_ON = "SET(0;Power;1)\r\n"
    CMD_POWER_OFF = "SET(0;Power;0)\r\n"
    CMD_GET_POWER = "get(0;Power)\r\n"
    
    def __init__(self, ip: str, port: int = 7992, timeout: int = 10):
        super().__init__(ip, port, timeout)
    
    async def _send_command(self, command: str) -> DeviceResult:
        """Send a command and get response."""
        start_time = time.time()
        
        try:
            # Connect to device
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.ip, self.port),
                timeout=self.timeout
            )
            
            # Send command
            writer.write(command.encode('utf-8'))
            await writer.drain()
            
            # Wait for device to process
            await asyncio.sleep(0.3)
            
            # Try to read response
            try:
                response = await asyncio.wait_for(
                    reader.read(512),
                    timeout=3
                )
                response_text = response.decode('utf-8', errors='ignore').strip()
            except asyncio.TimeoutError:
                response_text = ""
            
            # Close connection
            writer.close()
            await writer.wait_closed()
            
            duration_ms = int((time.time() - start_time) * 1000)
            
            return DeviceResult(
                success=True,
                message="Command sent successfully",
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
            
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return DeviceResult(
                success=False,
                message="Connection error",
                duration_ms=duration_ms,
                error=str(e)
            )
    
    async def turn_on(self) -> DeviceResult:
        """Turn on the Cubes system."""
        result = await self._send_command(self.CMD_POWER_ON)
        if result.success:
            result.power_state = PowerState.ON
            result.message = "Cubes system powering on"
        return result
    
    async def turn_off(self) -> DeviceResult:
        """Turn off the Cubes system."""
        result = await self._send_command(self.CMD_POWER_OFF)
        if result.success:
            result.power_state = PowerState.OFF
            result.message = "Cubes system powering off"
        return result
    
    async def get_status(self) -> DeviceResult:
        """Get Cubes system power status."""
        result = await self._send_command(self.CMD_GET_POWER)
        
        if result.success and result.response:
            # Parse response to determine power state
            response = result.response.lower()
            if "1" in response or "on" in response:
                result.power_state = PowerState.ON
            elif "0" in response or "off" in response:
                result.power_state = PowerState.OFF
            else:
                result.power_state = PowerState.UNKNOWN
        
        return result
