"""
Network checker for device reachability.
Supports ICMP ping and TCP port checks.
"""

import asyncio
import subprocess
import socket
import time
from typing import Tuple

from .base import DeviceResult, PowerState


class NetworkChecker:
    """
    Network connectivity checker for devices.
    
    Provides ping and TCP port checking capabilities.
    """
    
    def __init__(self, timeout: int = 5):
        self.timeout = timeout
    
    async def ping(self, ip: str) -> Tuple[bool, int]:
        """
        Ping an IP address.
        
        Returns:
            Tuple of (success, latency_ms)
        """
        start_time = time.time()
        
        try:
            # Use Windows ping command with count=1 and timeout
            process = await asyncio.create_subprocess_exec(
                "ping", "-n", "1", "-w", str(self.timeout * 1000), ip,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.timeout + 1
            )
            
            duration_ms = int((time.time() - start_time) * 1000)
            
            # Check if ping was successful
            success = process.returncode == 0
            
            return success, duration_ms
            
        except asyncio.TimeoutError:
            duration_ms = int((time.time() - start_time) * 1000)
            return False, duration_ms
            
        except Exception:
            duration_ms = int((time.time() - start_time) * 1000)
            return False, duration_ms
    
    async def check_tcp_port(self, ip: str, port: int) -> Tuple[bool, int]:
        """
        Check if a TCP port is open.
        
        Returns:
            Tuple of (success, latency_ms)
        """
        start_time = time.time()
        
        try:
            # Create TCP connection
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, port),
                timeout=self.timeout
            )
            
            # Close connection
            writer.close()
            await writer.wait_closed()
            
            duration_ms = int((time.time() - start_time) * 1000)
            return True, duration_ms
            
        except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
            duration_ms = int((time.time() - start_time) * 1000)
            return False, duration_ms
    
    async def check_device(self, ip: str, port: int = None) -> DeviceResult:
        """
        Check if device is reachable.
        
        First tries ping, then TCP port if specified.
        
        Returns:
            DeviceResult with reachability status
        """
        start_time = time.time()
        
        # Try ping first
        ping_success, ping_ms = await self.ping(ip)
        
        if ping_success:
            # If port specified, also check TCP
            if port:
                tcp_success, tcp_ms = await self.check_tcp_port(ip, port)
                
                if tcp_success:
                    total_ms = int((time.time() - start_time) * 1000)
                    return DeviceResult(
                        success=True,
                        message=f"Device reachable (ping: {ping_ms}ms, tcp: {tcp_ms}ms)",
                        duration_ms=total_ms,
                        power_state=PowerState.UNKNOWN
                    )
                else:
                    total_ms = int((time.time() - start_time) * 1000)
                    return DeviceResult(
                        success=False,
                        message=f"Ping OK but port {port} closed",
                        duration_ms=total_ms,
                        error=f"TCP port {port} not responding",
                        power_state=PowerState.UNKNOWN
                    )
            else:
                return DeviceResult(
                    success=True,
                    message=f"Device reachable (ping: {ping_ms}ms)",
                    duration_ms=ping_ms,
                    power_state=PowerState.UNKNOWN
                )
        else:
            total_ms = int((time.time() - start_time) * 1000)
            return DeviceResult(
                success=False,
                message="Device not reachable",
                duration_ms=total_ms,
                error=f"No ping response from {ip}",
                power_state=PowerState.UNKNOWN
            )


# Global instance
network_checker = NetworkChecker()
