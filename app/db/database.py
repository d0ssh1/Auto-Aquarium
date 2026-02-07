"""
SQLite database operations for Ocean Control System.
"""

import aiosqlite
from pathlib import Path
from datetime import datetime, date
from typing import Optional, List, Dict, Any

import structlog

logger = structlog.get_logger()

# Database file path
DB_PATH = Path("data/ocean.db")


async def get_db() -> aiosqlite.Connection:
    """Get database connection."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db


async def init_db() -> None:
    """Initialize database with schema."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    async with aiosqlite.connect(DB_PATH) as db:
        # Create devices table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                group_id TEXT NOT NULL,
                device_type TEXT NOT NULL,
                ip TEXT NOT NULL,
                port INTEGER DEFAULT NULL,
                mac TEXT DEFAULT NULL,
                enabled INTEGER DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create device_status table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS device_status (
                device_id TEXT PRIMARY KEY REFERENCES devices(id),
                is_online INTEGER DEFAULT 0,
                power_state TEXT DEFAULT 'unknown',
                last_check DATETIME,
                last_success DATETIME,
                last_error TEXT,
                consecutive_failures INTEGER DEFAULT 0
            )
        """)
        
        # Create action_logs table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS action_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                device_id TEXT REFERENCES devices(id),
                action TEXT NOT NULL,
                trigger_type TEXT NOT NULL,
                success INTEGER NOT NULL,
                attempt_number INTEGER DEFAULT 1,
                duration_ms INTEGER,
                error_message TEXT,
                extra_data TEXT
            )
        """)
        
        # Create schedule_config table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS schedule_config (
                id INTEGER PRIMARY KEY,
                on_time TEXT NOT NULL DEFAULT '09:00',
                off_time TEXT NOT NULL DEFAULT '20:00',
                timezone TEXT NOT NULL DEFAULT 'Asia/Vladivostok',
                enabled INTEGER DEFAULT 1,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create daily_reports table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS daily_reports (
                date DATE PRIMARY KEY,
                total_devices INTEGER,
                successful_on INTEGER,
                failed_on INTEGER,
                successful_off INTEGER,
                failed_off INTEGER,
                devices_with_retries TEXT,
                report_json TEXT,
                generated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create indexes
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_action_logs_timestamp 
            ON action_logs(timestamp)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_action_logs_device 
            ON action_logs(device_id)
        """)
        
        # Insert default schedule if not exists
        await db.execute("""
            INSERT OR IGNORE INTO schedule_config (id, on_time, off_time, timezone, enabled)
            VALUES (1, '09:00', '20:00', 'Asia/Vladivostok', 1)
        """)
        
        await db.commit()
        logger.info("database_initialized", path=str(DB_PATH))


# Device operations

async def get_all_devices() -> List[Dict[str, Any]]:
    """Get all devices from database."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT d.*, ds.is_online, ds.power_state, ds.last_check
            FROM devices d
            LEFT JOIN device_status ds ON d.id = ds.device_id
            ORDER BY d.group_id, d.name
        """)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_device(device_id: str) -> Optional[Dict[str, Any]]:
    """Get a single device by ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT d.*, ds.is_online, ds.power_state, ds.last_check, ds.last_error
            FROM devices d
            LEFT JOIN device_status ds ON d.id = ds.device_id
            WHERE d.id = ?
        """, (device_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def upsert_device(device: Dict[str, Any]) -> None:
    """Insert or update a device."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO devices (id, name, group_id, device_type, ip, port, mac, enabled)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                group_id = excluded.group_id,
                device_type = excluded.device_type,
                ip = excluded.ip,
                port = excluded.port,
                mac = excluded.mac,
                enabled = excluded.enabled,
                updated_at = CURRENT_TIMESTAMP
        """, (
            device["id"],
            device["name"],
            device["group"],
            device["type"],
            device["ip"],
            device.get("port"),
            device.get("mac"),
            1 if device.get("enabled", True) else 0
        ))
        await db.commit()


async def update_device_status(
    device_id: str,
    is_online: bool,
    power_state: str,
    error: Optional[str] = None
) -> None:
    """Update device status."""
    async with aiosqlite.connect(DB_PATH) as db:
        now = datetime.now().isoformat()
        
        if is_online:
            await db.execute("""
                INSERT INTO device_status (device_id, is_online, power_state, last_check, last_success, consecutive_failures)
                VALUES (?, 1, ?, ?, ?, 0)
                ON CONFLICT(device_id) DO UPDATE SET
                    is_online = 1,
                    power_state = excluded.power_state,
                    last_check = excluded.last_check,
                    last_success = excluded.last_success,
                    consecutive_failures = 0
            """, (device_id, power_state, now, now))
        else:
            await db.execute("""
                INSERT INTO device_status (device_id, is_online, power_state, last_check, last_error, consecutive_failures)
                VALUES (?, 0, ?, ?, ?, 1)
                ON CONFLICT(device_id) DO UPDATE SET
                    is_online = 0,
                    power_state = excluded.power_state,
                    last_check = excluded.last_check,
                    last_error = excluded.last_error,
                    consecutive_failures = device_status.consecutive_failures + 1
            """, (device_id, power_state, now, error))
        
        await db.commit()


# Action log operations

async def log_action(
    device_id: str,
    action: str,
    trigger_type: str,
    success: bool,
    attempt_number: int = 1,
    duration_ms: int = 0,
    error_message: Optional[str] = None,
    extra_data: Optional[str] = None
) -> None:
    """Log a device action."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO action_logs 
            (device_id, action, trigger_type, success, attempt_number, duration_ms, error_message, extra_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            device_id, action, trigger_type, 
            1 if success else 0, attempt_number, duration_ms, 
            error_message, extra_data
        ))
        await db.commit()


async def get_logs_by_date(log_date: date) -> List[Dict[str, Any]]:
    """Get action logs for a specific date."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT al.*, d.name as device_name
            FROM action_logs al
            LEFT JOIN devices d ON al.device_id = d.id
            WHERE date(al.timestamp) = ?
            ORDER BY al.timestamp DESC
        """, (log_date.isoformat(),))
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_device_logs(device_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    """Get action logs for a specific device."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT * FROM action_logs
            WHERE device_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (device_id, limit))
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


# Schedule operations

async def get_schedule() -> Dict[str, Any]:
    """Get current schedule configuration."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM schedule_config WHERE id = 1")
        row = await cursor.fetchone()
        return dict(row) if row else {
            "on_time": "09:00",
            "off_time": "20:00",
            "timezone": "Asia/Vladivostok",
            "enabled": True
        }


async def update_schedule(on_time: str, off_time: str, enabled: bool = True) -> None:
    """Update schedule configuration."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE schedule_config 
            SET on_time = ?, off_time = ?, enabled = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = 1
        """, (on_time, off_time, 1 if enabled else 0))
        await db.commit()


# Report operations

async def get_daily_report(report_date: date) -> Optional[Dict[str, Any]]:
    """Get daily report for a specific date."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT * FROM daily_reports WHERE date = ?
        """, (report_date.isoformat(),))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def save_daily_report(report: Dict[str, Any]) -> None:
    """Save daily report."""
    import json
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO daily_reports 
            (date, total_devices, successful_on, failed_on, successful_off, failed_off, devices_with_retries, report_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                total_devices = excluded.total_devices,
                successful_on = excluded.successful_on,
                failed_on = excluded.failed_on,
                successful_off = excluded.successful_off,
                failed_off = excluded.failed_off,
                devices_with_retries = excluded.devices_with_retries,
                report_json = excluded.report_json,
                generated_at = CURRENT_TIMESTAMP
        """, (
            report["date"],
            report["total_devices"],
            report.get("successful_on", 0),
            report.get("failed_on", 0),
            report.get("successful_off", 0),
            report.get("failed_off", 0),
            json.dumps(report.get("devices_with_retries", []), ensure_ascii=False),
            json.dumps(report, ensure_ascii=False)
        ))
        await db.commit()
