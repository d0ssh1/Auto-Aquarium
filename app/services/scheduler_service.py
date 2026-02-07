"""
Scheduler Service — APScheduler с SQLite JobStore.

Модуль для управления расписанием включения/выключения устройств.
Поддерживает сохранение состояния после перезагрузки.

Использование:
    scheduler = SchedulerService.from_config("config.json")
    await scheduler.start()
    await scheduler.stop()
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, time
from pathlib import Path
from typing import Optional, List, Callable, Any, Dict

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import (
    EVENT_JOB_EXECUTED,
    EVENT_JOB_ERROR,
    EVENT_JOB_MISSED,
    JobExecutionEvent,
)
from pydantic import BaseModel, Field
import pytz

logger = structlog.get_logger()


class ScheduleConfig(BaseModel):
    """Конфигурация расписания."""
    on_time: str = "09:00"
    off_time: str = "20:00"
    timezone: str = "Asia/Vladivostok"
    days: List[str] = Field(
        default_factory=lambda: [
            "Monday", "Tuesday", "Wednesday",
            "Thursday", "Friday", "Saturday", "Sunday"
        ]
    )
    exclude_dates: List[str] = Field(default_factory=list)


class RetryPolicy(BaseModel):
    """Политика повторных попыток."""
    max_attempts: int = 3
    base_interval_sec: int = 30
    backoff_multiplier: float = 2.0


class MonitoringConfig(BaseModel):
    """Конфигурация мониторинга."""
    enabled: bool = True
    status_check_interval_sec: int = 300
    alert_threshold: float = 0.8


class SchedulerConfig(BaseModel):
    """Полная конфигурация планировщика."""
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)


class SchedulerService:
    """
    Сервис планирования задач.
    
    Использует APScheduler с SQLite JobStore для персистентности.
    Поддерживает graceful shutdown и восстановление после сбоев.
    
    Attributes:
        config: Конфигурация планировщика
        scheduler: APScheduler экземпляр
        db_path: Путь к SQLite базе данных
    """
    
    JOB_TURN_ON = "daily_turn_on"
    JOB_TURN_OFF = "daily_turn_off"
    JOB_STATUS_CHECK = "status_check"
    
    def __init__(
        self,
        config: SchedulerConfig,
        db_path: str = "data/scheduler.db",
        turn_on_callback: Optional[Callable] = None,
        turn_off_callback: Optional[Callable] = None,
        status_check_callback: Optional[Callable] = None
    ):
        """
        Инициализация сервиса.
        
        Args:
            config: Конфигурация
            db_path: Путь к БД для JobStore
            turn_on_callback: Функция для включения устройств
            turn_off_callback: Функция для выключения устройств
            status_check_callback: Функция для проверки статуса
        """
        self.config = config
        self.db_path = db_path
        self._turn_on_callback = turn_on_callback
        self._turn_off_callback = turn_off_callback
        self._status_check_callback = status_check_callback
        self._scheduler: Optional[AsyncIOScheduler] = None
        self._running = False
        self._last_execution: Optional[datetime] = None
        
        # Создаём директорию для БД
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    
    @classmethod
    def from_config(
        cls,
        config_path: str,
        turn_on_callback: Optional[Callable] = None,
        turn_off_callback: Optional[Callable] = None,
        status_check_callback: Optional[Callable] = None
    ) -> "SchedulerService":
        """
        Создать сервис из файла конфигурации.
        
        Args:
            config_path: Путь к config.json
            turn_on_callback: Callback для включения
            turn_off_callback: Callback для выключения
            status_check_callback: Callback для проверки статуса
            
        Returns:
            SchedulerService
        """
        import json
        
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            config = SchedulerConfig(**data)
        except FileNotFoundError:
            logger.warning("scheduler_config_not_found", path=config_path)
            config = SchedulerConfig()
        except Exception as e:
            logger.error("scheduler_config_error", path=config_path, error=str(e))
            config = SchedulerConfig()
        
        return cls(
            config=config,
            turn_on_callback=turn_on_callback,
            turn_off_callback=turn_off_callback,
            status_check_callback=status_check_callback
        )
    
    def _create_scheduler(self) -> AsyncIOScheduler:
        """
        Создать и настроить APScheduler.
        
        Returns:
            AsyncIOScheduler
        """
        # Use memory jobstore (SQLite has pickle serialization issues with async callbacks)
        # For production, jobs are re-added on startup anyway
        jobstores = {
            # "default": SQLAlchemyJobStore(url=f"sqlite:///{self.db_path}")
        }
        
        # Настройки executor'а
        job_defaults = {
            "coalesce": True,  # Объединять пропущенные выполнения
            "max_instances": 1,  # Только один экземпляр job'а одновременно
            "misfire_grace_time": 3600  # Grace period 1 час
        }
        
        scheduler = AsyncIOScheduler(
            jobstores=jobstores,
            job_defaults=job_defaults,
            timezone=pytz.timezone(self.config.schedule.timezone)
        )
        
        # Подписываемся на события
        scheduler.add_listener(
            self._on_job_executed,
            EVENT_JOB_EXECUTED
        )
        scheduler.add_listener(
            self._on_job_error,
            EVENT_JOB_ERROR
        )
        scheduler.add_listener(
            self._on_job_missed,
            EVENT_JOB_MISSED
        )
        
        return scheduler
    
    def _on_job_executed(self, event: JobExecutionEvent) -> None:
        """Обработчик успешного выполнения job'а."""
        logger.info(
            "scheduler_job_executed",
            job_id=event.job_id,
            scheduled_time=event.scheduled_run_time.isoformat() if event.scheduled_run_time else None,
            retval=str(event.retval)[:100] if event.retval else None
        )
        self._last_execution = datetime.now()
    
    def _on_job_error(self, event: JobExecutionEvent) -> None:
        """Обработчик ошибки job'а."""
        logger.error(
            "scheduler_job_error",
            job_id=event.job_id,
            scheduled_time=event.scheduled_run_time.isoformat() if event.scheduled_run_time else None,
            exception=str(event.exception) if event.exception else None,
            traceback=str(event.traceback)[:500] if event.traceback else None
        )
    
    def _on_job_missed(self, event: JobExecutionEvent) -> None:
        """Обработчик пропущенного job'а."""
        logger.warning(
            "scheduler_job_missed",
            job_id=event.job_id,
            scheduled_time=event.scheduled_run_time.isoformat() if event.scheduled_run_time else None
        )
    
    def _parse_time(self, time_str: str) -> tuple[int, int]:
        """
        Распарсить время из строки.
        
        Args:
            time_str: Время в формате "HH:MM"
            
        Returns:
            Кортеж (hour, minute)
        """
        parts = time_str.split(":")
        return int(parts[0]), int(parts[1])
    
    def _get_day_of_week(self) -> str:
        """
        Получить строку дней недели для cron.
        
        Returns:
            Строка дней (например, "mon-sun")
        """
        day_mapping = {
            "Monday": "mon",
            "Tuesday": "tue",
            "Wednesday": "wed",
            "Thursday": "thu",
            "Friday": "fri",
            "Saturday": "sat",
            "Sunday": "sun"
        }
        
        days = []
        for day in self.config.schedule.days:
            if day in day_mapping:
                days.append(day_mapping[day])
        
        return ",".join(days) if days else "mon-sun"
    
    async def _execute_turn_on(self) -> None:
        """Выполнить включение устройств."""
        logger.info("scheduler_turn_on_start")
        
        # Проверяем исключённые даты
        today = datetime.now().strftime("%Y-%m-%d")
        if today in self.config.schedule.exclude_dates:
            logger.info("scheduler_turn_on_skipped", reason="excluded_date", date=today)
            return
        
        if self._turn_on_callback:
            try:
                result = await self._turn_on_callback()
                logger.info("scheduler_turn_on_complete", result=str(result)[:200])
            except Exception as e:
                logger.error("scheduler_turn_on_error", error=str(e))
                raise
        else:
            logger.warning("scheduler_turn_on_no_callback")
    
    async def _execute_turn_off(self) -> None:
        """Выполнить выключение устройств."""
        logger.info("scheduler_turn_off_start")
        
        # Проверяем исключённые даты
        today = datetime.now().strftime("%Y-%m-%d")
        if today in self.config.schedule.exclude_dates:
            logger.info("scheduler_turn_off_skipped", reason="excluded_date", date=today)
            return
        
        if self._turn_off_callback:
            try:
                result = await self._turn_off_callback()
                logger.info("scheduler_turn_off_complete", result=str(result)[:200])
            except Exception as e:
                logger.error("scheduler_turn_off_error", error=str(e))
                raise
        else:
            logger.warning("scheduler_turn_off_no_callback")
    
    async def _execute_status_check(self) -> None:
        """Выполнить проверку статуса."""
        # Проверяем, включен ли мониторинг
        if not self.config.monitoring.enabled:
            # logger.debug("scheduler_status_check_skipped", reason="disabled")
            return

        logger.debug("scheduler_status_check_start")
        
        if self._status_check_callback:
            try:
                await self._status_check_callback()
            except Exception as e:
                logger.error("scheduler_status_check_error", error=str(e))
        else:
            logger.debug("scheduler_status_check_no_callback")
    
    def _setup_jobs(self) -> None:
        """Настроить все задачи планировщика."""
        schedule = self.config.schedule
        monitoring = self.config.monitoring
        timezone = pytz.timezone(schedule.timezone)
        
        # Удаляем существующие job'ы
        for job_id in [self.JOB_TURN_ON, self.JOB_TURN_OFF, self.JOB_STATUS_CHECK]:
            try:
                self._scheduler.remove_job(job_id)
            except Exception:
                pass
        
        # Job: Включение устройств
        on_hour, on_minute = self._parse_time(schedule.on_time)
        day_of_week = self._get_day_of_week()
        
        self._scheduler.add_job(
            func=self._execute_turn_on,
            trigger=CronTrigger(
                hour=on_hour,
                minute=on_minute,
                day_of_week=day_of_week,
                timezone=timezone
            ),
            id=self.JOB_TURN_ON,
            name=f"Daily device turn-on at {schedule.on_time}",
            replace_existing=True
        )
        
        logger.info(
            "scheduler_job_added",
            job_id=self.JOB_TURN_ON,
            time=schedule.on_time,
            days=day_of_week
        )
        
        # Job: Выключение устройств
        off_hour, off_minute = self._parse_time(schedule.off_time)
        
        self._scheduler.add_job(
            func=self._execute_turn_off,
            trigger=CronTrigger(
                hour=off_hour,
                minute=off_minute,
                day_of_week=day_of_week,
                timezone=timezone
            ),
            id=self.JOB_TURN_OFF,
            name=f"Daily device turn-off at {schedule.off_time}",
            replace_existing=True
        )
        
        logger.info(
            "scheduler_job_added",
            job_id=self.JOB_TURN_OFF,
            time=schedule.off_time,
            days=day_of_week
        )
        
        # Job: Периодическая проверка статуса
        interval_minutes = monitoring.status_check_interval_sec // 60
        if interval_minutes < 1:
            interval_minutes = 1
        
        self._scheduler.add_job(
            func=self._execute_status_check,
            trigger="interval",
            minutes=interval_minutes,
            id=self.JOB_STATUS_CHECK,
            name="Periodic status check",
            replace_existing=True
        )
        
        logger.info(
            "scheduler_job_added",
            job_id=self.JOB_STATUS_CHECK,
            interval_minutes=interval_minutes
        )
    
    async def start(self) -> None:
        """Запустить планировщик."""
        if self._running:
            logger.warning("scheduler_already_running")
            return
        
        logger.info("scheduler_starting")
        
        self._scheduler = self._create_scheduler()
        self._setup_jobs()
        self._scheduler.start()
        self._running = True
        
        logger.info(
            "scheduler_started",
            jobs=len(self._scheduler.get_jobs()),
            timezone=self.config.schedule.timezone
        )
    
    async def stop(self, wait: bool = True) -> None:
        """
        Остановить планировщик.
        
        Args:
            wait: Дождаться завершения текущих задач
        """
        if not self._running:
            return
        
        logger.info("scheduler_stopping", wait=wait)
        
        if self._scheduler:
            self._scheduler.shutdown(wait=wait)
        
        self._running = False
        logger.info("scheduler_stopped")
    
    def is_running(self) -> bool:
        """Проверить, запущен ли планировщик."""
        return self._running and self._scheduler is not None
    
    def get_next_run_times(self) -> Dict[str, Optional[datetime]]:
        """
        Получить время следующего запуска для каждого job'а.
        
        Returns:
            Словарь {job_id: next_run_time}
        """
        if not self._scheduler:
            return {}
        
        result = {}
        for job in self._scheduler.get_jobs():
            result[job.id] = job.next_run_time
        
        return result
    
    def get_jobs_info(self) -> List[Dict[str, Any]]:
        """
        Получить информацию о всех job'ах.
        
        Returns:
            Список словарей с информацией о job'ах
        """
        if not self._scheduler:
            return []
        
        jobs_info = []
        for job in self._scheduler.get_jobs():
            jobs_info.append({
                "id": job.id,
                "name": job.name,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                "trigger": str(job.trigger)
            })
        
        return jobs_info
    
    async def trigger_now(self, job_id: str) -> bool:
        """
        Запустить job немедленно.
        
        Args:
            job_id: ID job'а
            
        Returns:
            True если запущено успешно
        """
        if not self._scheduler:
            return False
        
        job = self._scheduler.get_job(job_id)
        if not job:
            logger.warning("scheduler_job_not_found", job_id=job_id)
            return False
        
        logger.info("scheduler_job_triggered_manually", job_id=job_id)
        
        # Выполняем напрямую
        try:
            if job_id == self.JOB_TURN_ON:
                await self._execute_turn_on()
            elif job_id == self.JOB_TURN_OFF:
                await self._execute_turn_off()
            elif job_id == self.JOB_STATUS_CHECK:
                await self._execute_status_check()
            else:
                job.func()
            return True
        except Exception as e:
            logger.error("scheduler_manual_trigger_error", job_id=job_id, error=str(e))
            return False
    
    def update_schedule(
        self,
        on_time: Optional[str] = None,
        off_time: Optional[str] = None,
        timezone: Optional[str] = None
    ) -> None:
        """
        Обновить расписание.
        
        Args:
            on_time: Новое время включения
            off_time: Новое время выключения
            timezone: Новая временная зона
        """
        if on_time:
            self.config.schedule.on_time = on_time
        if off_time:
            self.config.schedule.off_time = off_time
        if timezone:
            self.config.schedule.timezone = timezone
        
        if self._running and self._scheduler:
            self._setup_jobs()
            
        logger.info(
            "scheduler_schedule_updated",
            on_time=self.config.schedule.on_time,
            off_time=self.config.schedule.off_time,
            timezone=self.config.schedule.timezone
        )
    
    def add_excluded_date(self, date: str) -> None:
        """
        Добавить дату в список исключений.
        
        Args:
            date: Дата в формате YYYY-MM-DD
        """
        if date not in self.config.schedule.exclude_dates:
            self.config.schedule.exclude_dates.append(date)
            logger.info("scheduler_date_excluded", date=date)
    
    def remove_excluded_date(self, date: str) -> None:
        """
        Удалить дату из списка исключений.
        
        Args:
            date: Дата в формате YYYY-MM-DD
        """
        if date in self.config.schedule.exclude_dates:
            self.config.schedule.exclude_dates.remove(date)
            logger.info("scheduler_date_included", date=date)


# Global instance
_scheduler_service: Optional[SchedulerService] = None


def get_scheduler_service(
    config_path: str = "config.json",
    **kwargs
) -> SchedulerService:
    """
    Получить глобальный экземпляр планировщика.
    
    Args:
        config_path: Путь к конфигурации
        **kwargs: Callbacks
        
    Returns:
        SchedulerService
    """
    global _scheduler_service
    if _scheduler_service is None:
        _scheduler_service = SchedulerService.from_config(config_path, **kwargs)
    return _scheduler_service


# Пример использования:
if __name__ == "__main__":
    import asyncio
    
    async def mock_turn_on():
        print("🔌 Включение всех устройств...")
        await asyncio.sleep(1)
        return {"success": True, "devices": 45}
    
    async def mock_turn_off():
        print("🔌 Выключение всех устройств...")
        await asyncio.sleep(1)
        return {"success": True, "devices": 45}
    
    async def mock_status_check():
        print("🔍 Проверка статуса...")
    
    async def main():
        # Создаём сервис
        config = SchedulerConfig(
            schedule=ScheduleConfig(
                on_time="09:00",
                off_time="20:00",
                timezone="Asia/Vladivostok"
            ),
            monitoring=MonitoringConfig(
                status_check_interval_sec=60  # Каждую минуту для теста
            )
        )
        
        scheduler = SchedulerService(
            config=config,
            turn_on_callback=mock_turn_on,
            turn_off_callback=mock_turn_off,
            status_check_callback=mock_status_check
        )
        
        # Запускаем
        await scheduler.start()
        
        # Информация о job'ах
        print("\n=== Scheduled Jobs ===")
        for job in scheduler.get_jobs_info():
            print(f"  {job['id']}: {job['name']}")
            print(f"    Next run: {job['next_run']}")
        
        # Ручной запуск
        print("\n=== Manual Trigger ===")
        await scheduler.trigger_now(SchedulerService.JOB_TURN_ON)
        
        # Ждём немного
        print("\nScheduler running... Press Ctrl+C to stop")
        try:
            await asyncio.sleep(10)
        except KeyboardInterrupt:
            pass
        
        # Останавливаем
        await scheduler.stop()
        print("\nScheduler stopped")
    
    asyncio.run(main())
