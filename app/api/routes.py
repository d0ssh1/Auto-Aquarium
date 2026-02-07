"""
API route definitions.
"""

from datetime import date, datetime
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services.device_manager import device_manager
from services.group_executor import group_executor
from services.scheduler import get_scheduler
from db import database
from core.config import get_config, reload_config

router = APIRouter()


# ============ Pydantic Schemas ============

class DeviceResponse(BaseModel):
    id: str
    name: str
    group: str
    type: str
    ip: str
    port: Optional[int] = None
    enabled: bool
    is_online: Optional[bool] = None
    power_state: Optional[str] = None
    last_check: Optional[str] = None


class ActionResponse(BaseModel):
    success: bool
    message: str
    device_id: str
    device_name: str
    attempts: int
    duration_ms: int
    error: Optional[str] = None


class BatchActionResponse(BaseModel):
    total: int
    successful: int
    failed: int
    duration_ms: int
    results: List[ActionResponse]


class ScheduleResponse(BaseModel):
    on_time: str
    off_time: str
    enabled: bool
    next_on: Optional[str] = None
    next_off: Optional[str] = None


class ScheduleUpdateRequest(BaseModel):
    on_time: str
    off_time: str
    enabled: bool = True


class LogEntry(BaseModel):
    id: int
    timestamp: str
    device_id: str
    device_name: Optional[str] = None
    action: str
    trigger_type: str
    success: bool
    attempt_number: int
    duration_ms: Optional[int] = None
    error_message: Optional[str] = None


class DailyReportResponse(BaseModel):
    date: str
    total_devices: int
    successful_on: int
    failed_on: int
    successful_off: int
    failed_off: int
    devices_with_retries: List[dict]


# ============ Device Endpoints ============

@router.get("/devices", response_model=List[DeviceResponse])
async def get_devices():
    """Get all devices with their current status."""
    config = get_config()
    db_devices = await database.get_all_devices()
    
    # Merge config and database data
    result = []
    db_map = {d["id"]: d for d in db_devices}
    
    for device in config.devices:
        db_data = db_map.get(device.id, {})
        result.append(DeviceResponse(
            id=device.id,
            name=device.name,
            group=device.group,
            type=device.type,
            ip=device.ip,
            port=device.port,
            enabled=device.enabled,
            is_online=db_data.get("is_online"),
            power_state=db_data.get("power_state"),
            last_check=db_data.get("last_check")
        ))
    
    return result


@router.get("/devices/{device_id}", response_model=DeviceResponse)
async def get_device(device_id: str):
    """Get a single device by ID."""
    config = get_config()
    device = next((d for d in config.devices if d.id == device_id), None)
    
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    
    db_data = await database.get_device(device_id) or {}
    
    return DeviceResponse(
        id=device.id,
        name=device.name,
        group=device.group,
        type=device.type,
        ip=device.ip,
        port=device.port,
        enabled=device.enabled,
        is_online=db_data.get("is_online"),
        power_state=db_data.get("power_state"),
        last_check=db_data.get("last_check")
    )


@router.get("/devices/{device_id}/status", response_model=ActionResponse)
async def check_device_status(device_id: str):
    """Check the status of a device."""
    result = await device_manager.get_status(device_id)
    
    return ActionResponse(
        success=result.success,
        message=result.message,
        device_id=result.device_id,
        device_name=result.device_name,
        attempts=result.attempts,
        duration_ms=result.duration_ms,
        error=result.error
    )


@router.post("/devices/{device_id}/on", response_model=ActionResponse)
async def turn_on_device(device_id: str):
    """Turn on a single device."""
    result = await device_manager.turn_on(device_id, trigger="manual")
    
    return ActionResponse(
        success=result.success,
        message=result.message,
        device_id=result.device_id,
        device_name=result.device_name,
        attempts=result.attempts,
        duration_ms=result.duration_ms,
        error=result.error
    )


@router.post("/devices/{device_id}/off", response_model=ActionResponse)
async def turn_off_device(device_id: str):
    """Turn off a single device."""
    result = await device_manager.turn_off(device_id, trigger="manual")
    
    return ActionResponse(
        success=result.success,
        message=result.message,
        device_id=result.device_id,
        device_name=result.device_name,
        attempts=result.attempts,
        duration_ms=result.duration_ms,
        error=result.error
    )


@router.post("/devices/group/{group_id}/on", response_model=BatchActionResponse)
async def turn_on_group(group_id: str):
    """Turn on all devices in a group."""
    devices = device_manager.get_devices_by_group(group_id)
    
    if not devices:
        raise HTTPException(status_code=404, detail=f"No devices found in group {group_id}")
    
    result = await group_executor.execute_group(
        devices=devices,
        action="turn_on",
        trigger="manual",
        parallel=True
    )
    
    return BatchActionResponse(
        total=result.total,
        successful=result.successful,
        failed=result.failed,
        duration_ms=result.duration_ms,
        results=[
            ActionResponse(
                success=r.success,
                message=r.message,
                device_id=r.device_id,
                device_name=r.device_name,
                attempts=r.attempts,
                duration_ms=r.duration_ms,
                error=r.error
            )
            for r in result.results
        ]
    )


@router.post("/devices/group/{group_id}/off", response_model=BatchActionResponse)
async def turn_off_group(group_id: str):
    """Turn off all devices in a group."""
    devices = device_manager.get_devices_by_group(group_id)
    
    if not devices:
        raise HTTPException(status_code=404, detail=f"No devices found in group {group_id}")
    
    result = await group_executor.execute_group(
        devices=devices,
        action="turn_off",
        trigger="manual",
        parallel=True
    )
    
    return BatchActionResponse(
        total=result.total,
        successful=result.successful,
        failed=result.failed,
        duration_ms=result.duration_ms,
        results=[
            ActionResponse(
                success=r.success,
                message=r.message,
                device_id=r.device_id,
                device_name=r.device_name,
                attempts=r.attempts,
                duration_ms=r.duration_ms,
                error=r.error
            )
            for r in result.results
        ]
    )


@router.post("/devices/all/on")
async def turn_on_all():
    """Turn on all devices by priority."""
    results = await group_executor.execute_all_by_priority(
        action="turn_on",
        trigger="manual"
    )
    
    total = sum(r.total for r in results.values())
    successful = sum(r.successful for r in results.values())
    failed = sum(r.failed for r in results.values())
    
    return {
        "success": failed == 0,
        "total": total,
        "successful": successful,
        "failed": failed,
        "groups": {
            group_id: {
                "total": r.total,
                "successful": r.successful,
                "failed": r.failed
            }
            for group_id, r in results.items()
        }
    }


@router.post("/devices/all/off")
async def turn_off_all():
    """Turn off all devices by priority."""
    results = await group_executor.execute_all_by_priority(
        action="turn_off",
        trigger="manual"
    )
    
    total = sum(r.total for r in results.values())
    successful = sum(r.successful for r in results.values())
    failed = sum(r.failed for r in results.values())
    
    return {
        "success": failed == 0,
        "total": total,
        "successful": successful,
        "failed": failed,
        "groups": {
            group_id: {
                "total": r.total,
                "successful": r.successful,
                "failed": r.failed
            }
            for group_id, r in results.items()
        }
    }


# ============ Schedule Endpoints ============

@router.get("/schedule", response_model=ScheduleResponse)
async def get_schedule():
    """Get current schedule configuration."""
    schedule = await database.get_schedule()
    scheduler = get_scheduler()
    next_runs = scheduler.get_next_run_times()
    
    return ScheduleResponse(
        on_time=schedule.get("on_time", "09:00"),
        off_time=schedule.get("off_time", "20:00"),
        enabled=bool(schedule.get("enabled", True)),
        next_on=next_runs.get("daily_turn_on", {}).get("next_run"),
        next_off=next_runs.get("daily_turn_off", {}).get("next_run")
    )


@router.put("/schedule", response_model=ScheduleResponse)
async def update_schedule(request: ScheduleUpdateRequest):
    """Update schedule configuration."""
    scheduler = get_scheduler()
    await scheduler.update_schedule(
        on_time=request.on_time,
        off_time=request.off_time,
        enabled=request.enabled
    )
    
    next_runs = scheduler.get_next_run_times()
    
    return ScheduleResponse(
        on_time=request.on_time,
        off_time=request.off_time,
        enabled=request.enabled,
        next_on=next_runs.get("daily_turn_on", {}).get("next_run"),
        next_off=next_runs.get("daily_turn_off", {}).get("next_run")
    )


@router.post("/schedule/enable")
async def enable_schedule():
    """Enable the schedule."""
    schedule = await database.get_schedule()
    scheduler = get_scheduler()
    await scheduler.update_schedule(
        on_time=schedule.get("on_time", "09:00"),
        off_time=schedule.get("off_time", "20:00"),
        enabled=True
    )
    return {"enabled": True}


@router.post("/schedule/disable")
async def disable_schedule():
    """Disable the schedule."""
    schedule = await database.get_schedule()
    scheduler = get_scheduler()
    await scheduler.update_schedule(
        on_time=schedule.get("on_time", "09:00"),
        off_time=schedule.get("off_time", "20:00"),
        enabled=False
    )
    return {"enabled": False}


# ============ Logs Endpoints ============

@router.get("/logs", response_model=List[LogEntry])
async def get_logs(log_date: Optional[str] = Query(None, alias="date")):
    """Get action logs for a specific date."""
    if log_date:
        try:
            parsed_date = date.fromisoformat(log_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    else:
        parsed_date = date.today()
    
    logs = await database.get_logs_by_date(parsed_date)
    
    return [
        LogEntry(
            id=log["id"],
            timestamp=log["timestamp"],
            device_id=log["device_id"],
            device_name=log.get("device_name"),
            action=log["action"],
            trigger_type=log["trigger_type"],
            success=bool(log["success"]),
            attempt_number=log["attempt_number"],
            duration_ms=log.get("duration_ms"),
            error_message=log.get("error_message")
        )
        for log in logs
    ]


@router.get("/logs/device/{device_id}", response_model=List[LogEntry])
async def get_device_logs(device_id: str, limit: int = Query(100, ge=1, le=1000)):
    """Get action logs for a specific device."""
    logs = await database.get_device_logs(device_id, limit)
    
    return [
        LogEntry(
            id=log["id"],
            timestamp=log["timestamp"],
            device_id=log["device_id"],
            device_name=None,
            action=log["action"],
            trigger_type=log["trigger_type"],
            success=bool(log["success"]),
            attempt_number=log["attempt_number"],
            duration_ms=log.get("duration_ms"),
            error_message=log.get("error_message")
        )
        for log in logs
    ]


# ============ Reports Endpoints ============

@router.get("/reports/daily", response_model=Optional[DailyReportResponse])
async def get_daily_report(report_date: Optional[str] = Query(None, alias="date")):
    """Get daily report for a specific date."""
    import json
    
    if report_date:
        try:
            parsed_date = date.fromisoformat(report_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    else:
        parsed_date = date.today()
    
    report = await database.get_daily_report(parsed_date)
    
    if not report:
        return None
    
    retries = []
    if report.get("devices_with_retries"):
        try:
            retries = json.loads(report["devices_with_retries"])
        except (json.JSONDecodeError, TypeError):
            retries = []
    
    return DailyReportResponse(
        date=report["date"],
        total_devices=report["total_devices"],
        successful_on=report.get("successful_on", 0),
        failed_on=report.get("failed_on", 0),
        successful_off=report.get("successful_off", 0),
        failed_off=report.get("failed_off", 0),
        devices_with_retries=retries
    )


# ============ System Endpoints ============

@router.get("/health")
async def health():
    """System health check."""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat()
    }


@router.get("/config")
async def get_current_config():
    """Get current configuration summary."""
    config = get_config()
    return {
        "devices_count": len(config.devices),
        "groups": [g.model_dump() for g in config.groups],
        "schedule": config.schedule.model_dump(),
        "retry": config.retry.model_dump()
    }


@router.post("/config/reload")
async def reload_configuration():
    """Reload configuration from file."""
    config = reload_config()
    device_manager.reload_config()
    return {
        "success": True,
        "devices_count": len(config.devices)
    }
