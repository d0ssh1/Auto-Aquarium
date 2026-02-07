"""
Group executor for parallel device operations.
"""

import asyncio
from typing import List, Dict, Any
from dataclasses import dataclass

import structlog

from core.config import get_config, DeviceConfig
from services.device_manager import device_manager, ActionResult

logger = structlog.get_logger()


@dataclass
class BatchResult:
    """Result of a batch operation."""
    total: int
    successful: int
    failed: int
    results: List[ActionResult]
    duration_ms: int


class GroupExecutor:
    """
    Executes device operations in parallel groups.
    
    Supports priority-based execution where higher priority
    groups complete before lower priority groups start.
    """
    
    def __init__(self):
        self.config = get_config()
    
    async def execute_group(
        self,
        devices: List[DeviceConfig],
        action: str,
        trigger: str = "scheduled",
        parallel: bool = True
    ) -> BatchResult:
        """
        Execute action on a group of devices.
        
        Args:
            devices: List of devices to operate on
            action: 'turn_on' or 'turn_off'
            trigger: Trigger type for logging
            parallel: If True, execute in parallel; otherwise sequential
        
        Returns:
            BatchResult with all operation results
        """
        import time
        start_time = time.time()
        
        if not devices:
            return BatchResult(
                total=0,
                successful=0,
                failed=0,
                results=[],
                duration_ms=0
            )
        
        if parallel:
            # Execute all devices in parallel
            if action == "turn_on":
                tasks = [device_manager.turn_on(d.id, trigger) for d in devices]
            else:
                tasks = [device_manager.turn_off(d.id, trigger) for d in devices]
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Process results
            action_results = []
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    action_results.append(ActionResult(
                        device_id=devices[i].id,
                        device_name=devices[i].name,
                        success=False,
                        message="Exception occurred",
                        attempts=0,
                        duration_ms=0,
                        error=str(result)
                    ))
                else:
                    action_results.append(result)
        else:
            # Execute sequentially
            action_results = []
            for device in devices:
                if action == "turn_on":
                    result = await device_manager.turn_on(device.id, trigger)
                else:
                    result = await device_manager.turn_off(device.id, trigger)
                action_results.append(result)
        
        duration_ms = int((time.time() - start_time) * 1000)
        successful = sum(1 for r in action_results if r.success)
        
        return BatchResult(
            total=len(devices),
            successful=successful,
            failed=len(devices) - successful,
            results=action_results,
            duration_ms=duration_ms
        )
    
    async def execute_all_by_priority(
        self,
        action: str,
        trigger: str = "scheduled"
    ) -> Dict[str, BatchResult]:
        """
        Execute action on all devices, respecting group priorities.
        
        Groups are executed in priority order (lower number = higher priority).
        Within each group, devices are executed in parallel.
        
        Returns:
            Dict mapping group_id to BatchResult
        """
        # Get groups sorted by priority
        groups = sorted(
            self.config.groups,
            key=lambda g: g.priority
        )
        
        all_results = {}
        
        for group in groups:
            devices = device_manager.get_devices_by_group(group.id)
            
            if not devices:
                logger.info(
                    "group_empty",
                    group=group.id,
                    action=action
                )
                continue
            
            logger.info(
                "group_execution_start",
                group=group.id,
                action=action,
                device_count=len(devices),
                parallel=group.parallel
            )
            
            result = await self.execute_group(
                devices=devices,
                action=action,
                trigger=trigger,
                parallel=group.parallel
            )
            
            all_results[group.id] = result
            
            logger.info(
                "group_execution_complete",
                group=group.id,
                action=action,
                successful=result.successful,
                failed=result.failed,
                duration_ms=result.duration_ms
            )
        
        return all_results


# Global instance
group_executor = GroupExecutor()
