"""
Ocean Aquarium Equipment Control System
Main Application Entry Point

Запуск:
    python main.py

Или через uvicorn:
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""

import asyncio
import io
import json
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import structlog
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Добавляем app в path
sys.path.insert(0, str(Path(__file__).parent / "app"))

# Local imports
from app.core.device_registry import DeviceRegistry, get_registry
from app.core.logger_service import get_logger_service, log_device_action
from app.services.scheduler_service import SchedulerService, SchedulerConfig, ScheduleConfig
from app.services.device_manager import DeviceManager, get_device_manager, ExecutionReport
from app.services.monitor_service import MonitorService, get_monitor_service, AlertLevel
from app.services.reports import ReportGenerator, get_report_generator

# ===== Configuration =====
CONFIG_PATH = Path(__file__).parent / "config.json"
STATIC_DIR = Path(__file__).parent / "app" / "static"
LOGS_DIR = Path(__file__).parent / "logs"
DATA_DIR = Path(__file__).parent / "data"

# Create directories
LOGS_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

# ===== Logging Setup =====
get_logger_service(log_dir=str(LOGS_DIR))
logger = structlog.get_logger()

# ===== Global Services =====
scheduler_service: Optional[SchedulerService] = None
device_manager: Optional[DeviceManager] = None
monitor_service: Optional[MonitorService] = None
report_generator: Optional[ReportGenerator] = None


# ===== Pydantic Models =====
class DeviceActionResponse(BaseModel):
    success: bool
    device_id: str
    action: str
    message: str
    duration_ms: Optional[int] = None


class BulkActionResponse(BaseModel):
    success: bool
    action: str
    total: int
    successful: int
    failed: int
    devices_with_errors: List[str] = []
    duration_seconds: float = 0


class ScheduleUpdateRequest(BaseModel):
    on_time: Optional[str] = None
    off_time: Optional[str] = None
    timezone: Optional[str] = None


class SettingsUpdateRequest(BaseModel):
    retry_policy: Optional[Dict[str, Any]] = None
    monitoring: Optional[Dict[str, Any]] = None


# ===== Scheduler Callbacks (module-level for pickle serialization) =====
async def on_turn_on():
    """Callback for scheduled turn on."""
    if device_manager:
        report = await device_manager.turn_on_all(parallel=True)
        if report_generator:
            report_generator.record_execution(report)
        return report

async def on_turn_off():
    """Callback for scheduled turn off."""
    if device_manager:
        report = await device_manager.turn_off_all(parallel=True)
        if report_generator:
            report_generator.record_execution(report)
        return report

async def on_status_check():
    """Callback for scheduled status check."""
    if monitor_service:
        result = await monitor_service.check_all_devices()
        if report_generator and "online_rate" in result:
            report_generator.record_online_rate(result["online_rate"])
        return result


# ===== Lifespan =====
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown."""
    global scheduler_service, device_manager, monitor_service, report_generator
    
    logger.info("app_starting", config_path=str(CONFIG_PATH))
    
    # Load config
    config = load_config()
    
    # Initialize services
    device_manager = DeviceManager.from_config(str(CONFIG_PATH))
    monitor_service = MonitorService.from_config(str(CONFIG_PATH))
    report_generator = ReportGenerator(reports_dir=str(DATA_DIR / "reports"))
    
    scheduler_config = SchedulerConfig(
        schedule=ScheduleConfig(
            on_time=config.get("schedule", {}).get("on_time", "09:00"),
            off_time=config.get("schedule", {}).get("off_time", "20:00"),
            timezone=config.get("schedule", {}).get("timezone", "Asia/Vladivostok"),
            days=config.get("schedule", {}).get("days", []),
            exclude_dates=config.get("schedule", {}).get("exclude_dates", [])
        ),
        monitoring=MonitoringConfig(
            enabled=config.get("monitoring", {}).get("enabled", True),
            status_check_interval_sec=config.get("monitoring", {}).get("status_check_interval_sec", 300),
            alert_threshold=config.get("monitoring", {}).get("alert_threshold", 0.8)
        )
    )
    
    scheduler_service = SchedulerService(
        config=scheduler_config,
        db_path=str(DATA_DIR / "scheduler.db"),
        turn_on_callback=on_turn_on,
        turn_off_callback=on_turn_off,
        status_check_callback=on_status_check
    )
    
    # Start scheduler
    await scheduler_service.start()
    
    logger.info(
        "app_started",
        devices=len(device_manager.registry.get_devices()),
        scheduler_jobs=len(scheduler_service.get_jobs_info())
    )
    
    yield
    
    # Shutdown
    logger.info("app_stopping")
    await scheduler_service.stop(wait=True)
    logger.info("app_stopped")


def load_config() -> Dict[str, Any]:
    """Load configuration from JSON file."""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error("config_load_error", error=str(e))
        return {}


# ===== FastAPI App =====
app = FastAPI(
    title="Ocean Aquarium Control System",
    description="Equipment management for oceanarium",
    version="1.0.0",
    lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ===== Health & Root =====
@app.get("/")
async def root():
    """Serve the main UI."""
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {"message": "Ocean Aquarium Control System", "status": "running"}


@app.get("/api/health")
async def health():
    """System health check."""
    registry = device_manager.registry if device_manager else None
    monitor = monitor_service
    
    devices_total = len(registry.get_devices()) if registry else 0
    
    # Get monitoring summary
    summary = monitor.get_summary() if monitor else {}
    
    return {
        "status": "running",
        "timestamp": datetime.now().isoformat(),
        "devices_total": devices_total,
        "devices_online": summary.get("online", 0),
        "success_rate": summary.get("online_rate", 1.0),
        "scheduler_running": scheduler_service.is_running() if scheduler_service else False
    }


# ===== Devices API =====
@app.get("/api/devices")
async def get_devices():
    """Get all devices with status."""
    if not device_manager:
        raise HTTPException(500, "Device manager not initialized")
    
    devices = device_manager.registry.get_devices()
    result = []
    
    for d in devices:
        # Get health record if available
        health = monitor_service.get_device_health(d.id) if monitor_service else None
        
        result.append({
            "id": d.id,
            "name": d.name,
            "ip": d.ip,
            "port": d.port,
            "type": d.device_type,
            "group": d.group,
            "enabled": d.enabled,
            "status": health.state.value if health else "unknown",
            "last_check": health.last_check.isoformat() if health and health.last_check else None
        })
    
    return result


@app.get("/api/devices/{device_id}")
async def get_device(device_id: str):
    """Get single device info."""
    if not device_manager:
        raise HTTPException(500, "Device manager not initialized")
    
    device = device_manager.registry.get_device(device_id)
    if not device:
        raise HTTPException(404, f"Device not found: {device_id}")
    
    health = monitor_service.get_device_health(device_id) if monitor_service else None
    
    return {
        "id": device.id,
        "name": device.name,
        "ip": device.ip,
        "port": device.port,
        "type": device.device_type,
        "group": device.group_id,
        "enabled": device.enabled,
        "status": health.state.value if health else "unknown",
        "last_check": health.last_check.isoformat() if health and health.last_check else None,
        "consecutive_failures": health.consecutive_failures if health else 0
    }


@app.post("/api/devices/{device_id}/on")
async def turn_on_device(device_id: str):
    """Turn on a single device."""
    if not device_manager:
        raise HTTPException(500, "Device manager not initialized")
    
    result = await device_manager.turn_on_device(device_id)
    
    log_device_action(
        device_id=device_id,
        device_name=result.device_name,
        device_ip=result.device_ip,
        action="TURN_ON",
        trigger="api",
        success=result.success,
        attempt=result.attempts,
        duration_ms=result.duration_ms,
        error=result.error
    )
    
    return DeviceActionResponse(
        success=result.success,
        device_id=device_id,
        action="TURN_ON",
        message="Success" if result.success else (result.error or "Failed"),
        duration_ms=result.duration_ms
    )


@app.post("/api/devices/{device_id}/off")
async def turn_off_device(device_id: str):
    """Turn off a single device."""
    if not device_manager:
        raise HTTPException(500, "Device manager not initialized")
    
    result = await device_manager.turn_off_device(device_id)
    
    log_device_action(
        device_id=device_id,
        device_name=result.device_name,
        device_ip=result.device_ip,
        action="TURN_OFF",
        trigger="api",
        success=result.success,
        attempt=result.attempts,
        duration_ms=result.duration_ms,
        error=result.error
    )
    
    return DeviceActionResponse(
        success=result.success,
        device_id=device_id,
        action="TURN_OFF",
        message="Success" if result.success else (result.error or "Failed"),
        duration_ms=result.duration_ms
    )


@app.post("/api/devices/all/on")
async def turn_on_all():
    """Turn on all devices."""
    if not device_manager:
        raise HTTPException(500, "Device manager not initialized")
    
    report = await device_manager.turn_on_all(parallel=True)
    
    if report_generator:
        report_generator.record_execution(report)
    
    return BulkActionResponse(
        success=report.status == "SUCCESS",
        action="TURN_ON",
        total=report.total_devices,
        successful=report.successful,
        failed=report.failed,
        devices_with_errors=report.devices_with_errors,
        duration_seconds=report.duration_seconds
    )


@app.post("/api/devices/all/off")
async def turn_off_all():
    """Turn off all devices."""
    if not device_manager:
        raise HTTPException(500, "Device manager not initialized")
    
    report = await device_manager.turn_off_all(parallel=True)
    
    if report_generator:
        report_generator.record_execution(report)
    
    return BulkActionResponse(
        success=report.status == "SUCCESS",
        action="TURN_OFF",
        total=report.total_devices,
        successful=report.successful,
        failed=report.failed,
        devices_with_errors=report.devices_with_errors,
        duration_seconds=report.duration_seconds
    )


# ===== Groups API =====
@app.get("/api/groups")
async def get_groups():
    """Get all device groups."""
    if not device_manager:
        raise HTTPException(500, "Device manager not initialized")
    
    groups = device_manager.registry.get_sorted_groups()
    return [
        {
            "id": g.id,
            "name": g.name,
            "description": g.description,
            "order": g.order
        }
        for g in groups
    ]


@app.get("/api/groups/status")
async def get_groups_status():
    """Get status summary for all groups."""
    if not device_manager or not monitor_service:
        raise HTTPException(500, "Services not initialized")
    
    groups = device_manager.registry.get_groups_sorted()
    result = []
    
    for group in groups:
        devices = device_manager.registry.get_by_group(group.id)
        online_count = 0
        
        for device in devices:
            health = monitor_service.get_device_health(device.id)
            if health and health.state.value == "online":
                online_count += 1
        
        result.append({
            "id": group.id,
            "name": group.name,
            "total": len(devices),
            "online": online_count
        })
    
    return result


@app.post("/api/groups/{group_id}/on")
async def turn_on_group(group_id: str):
    """Turn on all devices in a group."""
    if not device_manager:
        raise HTTPException(500, "Device manager not initialized")
    
    report = await device_manager.turn_on_group(group_id, parallel=True)
    
    return BulkActionResponse(
        success=report.status == "SUCCESS",
        action="TURN_ON",
        total=report.total_devices,
        successful=report.successful,
        failed=report.failed,
        devices_with_errors=report.devices_with_errors,
        duration_seconds=report.duration_seconds
    )


@app.post("/api/groups/{group_id}/off")
async def turn_off_group(group_id: str):
    """Turn off all devices in a group."""
    if not device_manager:
        raise HTTPException(500, "Device manager not initialized")
    
    report = await device_manager.turn_off_group(group_id, parallel=True)
    
    return BulkActionResponse(
        success=report.status == "SUCCESS",
        action="TURN_OFF",
        total=report.total_devices,
        successful=report.successful,
        failed=report.failed,
        devices_with_errors=report.devices_with_errors,
        duration_seconds=report.duration_seconds
    )


# ===== Schedule API =====
@app.get("/api/schedule")
async def get_schedule():
    """Get current schedule."""
    if not scheduler_service:
        raise HTTPException(500, "Scheduler not initialized")
    
    config = scheduler_service.config.schedule
    next_runs = scheduler_service.get_next_run_times()
    
    on_next = next_runs.get("daily_turn_on")
    off_next = next_runs.get("daily_turn_off")
    
    next_exec = None
    next_act = None
    
    if on_next and off_next:
        if on_next < off_next:
            next_exec = on_next
            next_act = "TURN_ON"
        else:
            next_exec = off_next
            next_act = "TURN_OFF"
    elif on_next:
        next_exec = on_next
        next_act = "TURN_ON"
    elif off_next:
        next_exec = off_next
        next_act = "TURN_OFF"
    
    return {
        "on_time": config.on_time,
        "off_time": config.off_time,
        "timezone": config.timezone,
        "days": config.days,
        "exclude_dates": config.exclude_dates,
        "next_execution": next_exec.isoformat() if next_exec else None,
        "next_action": next_act
    }


@app.post("/api/schedule")
async def update_schedule(request: ScheduleUpdateRequest):
    """Update schedule."""
    if not scheduler_service:
        raise HTTPException(500, "Scheduler not initialized")
    
    scheduler_service.update_schedule(
        on_time=request.on_time,
        off_time=request.off_time,
        timezone=request.timezone
    )
    
    return {"success": True, "message": "Schedule updated"}


@app.get("/api/schedule/jobs")
async def get_schedule_jobs():
    """Get scheduled jobs info."""
    if not scheduler_service:
        raise HTTPException(500, "Scheduler not initialized")
    
    return scheduler_service.get_jobs_info()


@app.post("/api/schedule/jobs/{job_id}/trigger")
async def trigger_job(job_id: str):
    """Trigger a job manually."""
    if not scheduler_service:
        raise HTTPException(500, "Scheduler not initialized")
    
    success = await scheduler_service.trigger_now(job_id)
    
    return {"success": success, "job_id": job_id}


# ===== Alerts API =====
@app.get("/api/alerts")
async def get_alerts(hours: int = Query(24, ge=1, le=168)):
    """Get recent alerts."""
    if not monitor_service:
        raise HTTPException(500, "Monitor not initialized")
    
    alerts = monitor_service.get_recent_alerts(hours=hours)
    
    return [a.to_dict() for a in alerts]


# ===== Logs API =====
@app.get("/api/logs")
async def get_logs(
    date: Optional[str] = None,
    level: Optional[str] = None,
    device: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200)
):
    """Get log entries."""
    # Read from actions.jsonl file
    log_file = LOGS_DIR / "actions.jsonl"
    
    if not log_file.exists():
        return {"logs": [], "total": 0, "page": page}
    
    logs = []
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    logs.append(entry)
                except:
                    continue
    except Exception as e:
        logger.error("logs_read_error", error=str(e))
        return {"logs": [], "total": 0, "page": page}
    
    # Filter
    if date:
        logs = [l for l in logs if l.get("timestamp", "").startswith(date)]
    if level:
        logs = [l for l in logs if l.get("level") == level]
    if device:
        logs = [l for l in logs if l.get("device_id") == device]
    
    # Sort by timestamp desc
    logs.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    
    # Paginate
    total = len(logs)
    start = (page - 1) * limit
    end = start + limit
    logs = logs[start:end]
    
    return {"logs": logs, "total": total, "page": page}


@app.get("/api/logs/export")
async def export_logs():
    """Export logs as JSON file."""
    log_file = LOGS_DIR / "actions.jsonl"
    
    if not log_file.exists():
        raise HTTPException(404, "Log file not found")
    
    return FileResponse(
        str(log_file),
        media_type="application/json",
        filename=f"logs_{datetime.now().strftime('%Y%m%d')}.jsonl"
    )


# ===== Settings API =====
@app.get("/api/settings")
async def get_settings():
    """Get current settings."""
    config = load_config()
    
    return {
        "retry_policy": config.get("retry_policy", {}),
        "monitoring": config.get("monitoring", {}),
        "zabbix": {
            "enabled": config.get("zabbix", {}).get("enabled", False),
            "url": config.get("zabbix", {}).get("url", "")
        }
    }


@app.post("/api/settings")
async def update_settings(request: SettingsUpdateRequest):
    """Update settings."""
    config = load_config()
    
    if request.retry_policy:
        config["retry_policy"] = {**config.get("retry_policy", {}), **request.retry_policy}
    if request.monitoring:
        config["monitoring"] = {**config.get("monitoring", {}), **request.monitoring}
    
    # Save config
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        return {"success": True}
    except Exception as e:
        logger.error("config_save_error", error=str(e))
        raise HTTPException(500, "Failed to save config")


@app.post("/api/config/reload")
async def reload_config():
    """Reload configuration."""
    global device_manager
    
    if device_manager:
        device_manager.registry.reload()
    
    return {"success": True, "message": "Config reloaded"}


# ===== Reports API =====
@app.get("/api/reports/daily")
async def get_daily_report(date: Optional[str] = None):
    """Get daily report."""
    if not report_generator:
        raise HTTPException(500, "Report generator not initialized")
    
    from datetime import date as date_type
    
    if date:
        report_date = date_type.fromisoformat(date)
    else:
        report_date = date_type.today()
    
    report = report_generator.generate_daily_report(report_date)
    
    return report.to_dict()


# ===== Main Entry Point =====
def create_app() -> FastAPI:
    """Create and configure the application."""
    return app


if __name__ == "__main__":
    # Load config for port settings
    config = load_config()
    app_config = config.get("app", {})
    
    host = app_config.get("host", "0.0.0.0")
    port = app_config.get("port", 8000)
    debug = app_config.get("debug", False)
    
    print(f"""
╔══════════════════════════════════════════════════════════╗
║     🌊 OCEAN AQUARIUM EQUIPMENT CONTROL SYSTEM 🌊        ║
╠══════════════════════════════════════════════════════════╣
║  Starting server...                                       ║
║  URL: http://{host}:{port}                                ║
║  Press Ctrl+C to stop                                    ║
╚══════════════════════════════════════════════════════════╝
    """)
    
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=debug,
        log_level="info"
    )
