"""
Scheduler service using APScheduler.
"""

import asyncio
from datetime import datetime, date
from typing import Optional

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from core.config import get_config
from db import database

logger = structlog.get_logger()


class SchedulerService:
    """
    Scheduler for automated device control.
    
    Manages scheduled turn on/off jobs using APScheduler.
    """
    
    def __init__(self):
        self.config = get_config()
        self.scheduler = AsyncIOScheduler(timezone=self.config.schedule.timezone)
        self._is_running = False
    
    async def start(self) -> None:
        """Start the scheduler."""
        if self._is_running:
            return
        
        # Load schedule from database
        schedule = await database.get_schedule()
        
        if schedule.get("enabled", True):
            await self._setup_jobs(
                on_time=schedule.get("on_time", "09:00"),
                off_time=schedule.get("off_time", "20:00")
            )
        
        self.scheduler.start()
        self._is_running = True
        logger.info("scheduler_started", timezone=self.config.schedule.timezone)
    
    async def stop(self) -> None:
        """Stop the scheduler."""
        if not self._is_running:
            return
        
        self.scheduler.shutdown(wait=False)
        self._is_running = False
        logger.info("scheduler_stopped")
    
    async def _setup_jobs(self, on_time: str, off_time: str) -> None:
        """Set up scheduled jobs."""
        # Parse times
        on_hour, on_minute = map(int, on_time.split(":"))
        off_hour, off_minute = map(int, off_time.split(":"))
        
        # Add morning turn-on job
        self.scheduler.add_job(
            func=self._scheduled_turn_on,
            trigger=CronTrigger(hour=on_hour, minute=on_minute),
            id="daily_turn_on",
            name="Ежедневное включение",
            replace_existing=True,
            misfire_grace_time=300
        )
        
        # Add evening turn-off job
        self.scheduler.add_job(
            func=self._scheduled_turn_off,
            trigger=CronTrigger(hour=off_hour, minute=off_minute),
            id="daily_turn_off",
            name="Ежедневное выключение",
            replace_existing=True,
            misfire_grace_time=300
        )
        
        logger.info(
            "jobs_scheduled",
            on_time=on_time,
            off_time=off_time
        )
    
    async def _scheduled_turn_on(self) -> None:
        """Execute scheduled turn on for all devices."""
        logger.info("scheduled_turn_on_start", time=datetime.now().isoformat())
        
        try:
            from services.group_executor import group_executor
            
            results = await group_executor.execute_all_by_priority(
                action="turn_on",
                trigger="scheduled"
            )
            
            # Generate summary
            total = sum(r.total for r in results.values())
            successful = sum(r.successful for r in results.values())
            failed = sum(r.failed for r in results.values())
            
            # Find devices that needed retries
            devices_with_retries = []
            for group_id, batch_result in results.items():
                for result in batch_result.results:
                    if result.attempts > 1:
                        devices_with_retries.append({
                            "device_id": result.device_id,
                            "device_name": result.device_name,
                            "attempts": result.attempts
                        })
            
            # Save daily report
            report = {
                "date": date.today().isoformat(),
                "total_devices": total,
                "successful_on": successful,
                "failed_on": failed,
                "devices_with_retries": devices_with_retries
            }
            await database.save_daily_report(report)
            
            logger.info(
                "scheduled_turn_on_complete",
                total=total,
                successful=successful,
                failed=failed,
                retries=len(devices_with_retries)
            )
            
        except Exception as e:
            logger.error("scheduled_turn_on_error", error=str(e))
    
    async def _scheduled_turn_off(self) -> None:
        """Execute scheduled turn off for all devices."""
        logger.info("scheduled_turn_off_start", time=datetime.now().isoformat())
        
        try:
            from services.group_executor import group_executor
            
            results = await group_executor.execute_all_by_priority(
                action="turn_off",
                trigger="scheduled"
            )
            
            # Generate summary
            total = sum(r.total for r in results.values())
            successful = sum(r.successful for r in results.values())
            failed = sum(r.failed for r in results.values())
            
            # Update daily report with off stats
            today = date.today()
            existing_report = await database.get_daily_report(today)
            
            if existing_report:
                import json
                report_data = json.loads(existing_report.get("report_json", "{}"))
                report_data["successful_off"] = successful
                report_data["failed_off"] = failed
                await database.save_daily_report(report_data)
            
            logger.info(
                "scheduled_turn_off_complete",
                total=total,
                successful=successful,
                failed=failed
            )
            
        except Exception as e:
            logger.error("scheduled_turn_off_error", error=str(e))
    
    async def update_schedule(self, on_time: str, off_time: str, enabled: bool = True) -> None:
        """Update schedule configuration."""
        # Save to database
        await database.update_schedule(on_time, off_time, enabled)
        
        # Remove existing jobs
        try:
            self.scheduler.remove_job("daily_turn_on")
            self.scheduler.remove_job("daily_turn_off")
        except Exception:
            pass
        
        # Add new jobs if enabled
        if enabled:
            await self._setup_jobs(on_time, off_time)
        
        logger.info(
            "schedule_updated",
            on_time=on_time,
            off_time=off_time,
            enabled=enabled
        )
    
    def get_next_run_times(self) -> dict:
        """Get next scheduled run times."""
        jobs = {}
        
        for job in self.scheduler.get_jobs():
            next_run = job.next_run_time
            jobs[job.id] = {
                "name": job.name,
                "next_run": next_run.isoformat() if next_run else None
            }
        
        return jobs


# Global instance (created on first import, started in lifespan)
scheduler_service: Optional[SchedulerService] = None


def get_scheduler() -> SchedulerService:
    """Get scheduler service instance."""
    global scheduler_service
    if scheduler_service is None:
        scheduler_service = SchedulerService()
    return scheduler_service
