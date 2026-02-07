"""
Reports — Генерация отчётов.

Модуль для создания дневных отчётов и сводок:
- ExecutionReport: отчёт о выполнении операции
- DailyReport: дневная сводка
- Сохранение в файлы и БД

Использование:
    generator = ReportGenerator()
    daily = await generator.generate_daily_report()
    print(daily.to_text())
"""

import json
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger()


class DeviceExecutionDetail(BaseModel):
    """Детали выполнения для устройства."""
    device_id: str
    device_name: str
    status: str  # SUCCESS, FAILED, SKIPPED
    attempts: int = 1
    duration_ms: int = 0
    error: Optional[str] = None
    notes: Optional[str] = None


class ExecutionReport(BaseModel):
    """
    Отчёт о выполнении операции (включение/выключение).
    
    Используется для записи результатов каждого запуска.
    """
    timestamp: datetime
    action: str  # TURN_ON, TURN_OFF
    trigger: str = "scheduled"  # scheduled, manual, api
    total_devices: int = 0
    successful: int = 0
    failed: int = 0
    skipped: int = 0
    devices_with_retries: int = 0
    total_retry_count: int = 0
    duration_seconds: float = 0.0
    status: str = "SUCCESS"  # SUCCESS, PARTIAL, FAILED
    device_details: List[DeviceExecutionDetail] = Field(default_factory=list)
    
    class Config:
        use_enum_values = True
    
    @property
    def success_rate(self) -> float:
        """Процент успешных операций."""
        total = self.successful + self.failed
        return self.successful / max(total, 1)
    
    def to_text(self) -> str:
        """Генерировать текстовый отчёт."""
        lines = [
            f"EXECUTION REPORT — {self.timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 60,
            f"Action: {self.action}",
            f"Trigger: {self.trigger}",
            f"",
            f"Total Devices: {self.total_devices}",
            f"✅ Successful: {self.successful} ({self.success_rate:.1%})",
        ]
        
        if self.devices_with_retries > 0:
            lines.append(f"⚠️ Required Retries: {self.devices_with_retries} devices ({self.total_retry_count} total attempts)")
        
        if self.failed > 0:
            lines.append(f"❌ Failed: {self.failed}")
        
        if self.skipped > 0:
            lines.append(f"⏭️ Skipped: {self.skipped}")
        
        lines.extend([
            f"",
            f"Duration: {self.duration_seconds:.1f} seconds",
            f"Status: {self.status}",
        ])
        
        # Детали устройств
        if self.device_details:
            lines.append("")
            lines.append("Device Details:")
            lines.append("-" * 40)
            
            for detail in self.device_details:
                emoji = {
                    "SUCCESS": "✅",
                    "FAILED": "❌",
                    "SKIPPED": "⏭️"
                }.get(detail.status, "❓")
                
                line = f"  {emoji} {detail.device_id}: {detail.status}"
                
                if detail.attempts > 1:
                    line += f" ({detail.attempts} attempts)"
                
                if detail.error:
                    line += f" — {detail.error}"
                
                lines.append(line)
        
        # Recovery actions
        failed_devices = [d for d in self.device_details if d.status == "FAILED"]
        if failed_devices:
            lines.append("")
            lines.append("Recovery Actions:")
            lines.append("-" * 40)
            for device in failed_devices:
                lines.append(f"  ⚠️ Alert: {device.device_id} not responding — manual intervention may be required")
        
        return "\n".join(lines)
    
    def to_dict(self) -> Dict[str, Any]:
        """Конвертировать в словарь."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "action": self.action,
            "trigger": self.trigger,
            "total_devices": self.total_devices,
            "successful": self.successful,
            "failed": self.failed,
            "skipped": self.skipped,
            "devices_with_retries": self.devices_with_retries,
            "total_retry_count": self.total_retry_count,
            "duration_seconds": self.duration_seconds,
            "success_rate": self.success_rate,
            "status": self.status,
            "device_count": len(self.device_details)
        }


class AlertSummary(BaseModel):
    """Сводка алертов за день."""
    total: int = 0
    info: int = 0
    warning: int = 0
    critical: int = 0
    red_alert: int = 0
    
    @property
    def has_critical(self) -> bool:
        return self.critical > 0 or self.red_alert > 0


class DailyReport(BaseModel):
    """
    Дневной отчёт.
    
    Агрегирует все события за день.
    """
    report_date: date
    generated_at: datetime = Field(default_factory=datetime.now)
    
    # Утреннее включение
    morning_execution: Optional[ExecutionReport] = None
    
    # Вечернее выключение
    evening_execution: Optional[ExecutionReport] = None
    
    # Мониторинг
    monitoring_checks: int = 0
    average_online_rate: float = 1.0
    min_online_rate: float = 1.0
    
    # Алерты
    alerts: AlertSummary = Field(default_factory=AlertSummary)
    
    # Проблемные устройства
    problematic_devices: List[str] = Field(default_factory=list)
    
    # Общий статус дня
    day_status: str = "NORMAL"  # NORMAL, ISSUES, CRITICAL
    
    def to_text(self) -> str:
        """Генерировать текстовый отчёт."""
        lines = [
            f"DAILY REPORT — {self.report_date.strftime('%Y-%m-%d')}",
            "=" * 60,
            f"Generated: {self.generated_at.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Status: {self.day_status}",
            "",
        ]
        
        # Утреннее включение
        lines.append("📅 MORNING TURN-ON")
        lines.append("-" * 40)
        if self.morning_execution:
            me = self.morning_execution
            lines.append(f"  Time: {me.timestamp.strftime('%H:%M:%S')}")
            lines.append(f"  Devices: {me.successful}/{me.total_devices} successful ({me.success_rate:.1%})")
            if me.failed > 0:
                lines.append(f"  Failed: {me.failed}")
            lines.append(f"  Duration: {me.duration_seconds:.1f}s")
        else:
            lines.append("  ❌ No execution recorded")
        lines.append("")
        
        # Вечернее выключение
        lines.append("🌙 EVENING TURN-OFF")
        lines.append("-" * 40)
        if self.evening_execution:
            ee = self.evening_execution
            lines.append(f"  Time: {ee.timestamp.strftime('%H:%M:%S')}")
            lines.append(f"  Devices: {ee.successful}/{ee.total_devices} successful ({ee.success_rate:.1%})")
            if ee.failed > 0:
                lines.append(f"  Failed: {ee.failed}")
            lines.append(f"  Duration: {ee.duration_seconds:.1f}s")
        else:
            lines.append("  ⏳ Pending or not scheduled")
        lines.append("")
        
        # Мониторинг
        lines.append("📊 MONITORING")
        lines.append("-" * 40)
        lines.append(f"  Status checks: {self.monitoring_checks}")
        lines.append(f"  Average online rate: {self.average_online_rate:.1%}")
        lines.append(f"  Minimum online rate: {self.min_online_rate:.1%}")
        lines.append("")
        
        # Алерты
        lines.append("🚨 ALERTS")
        lines.append("-" * 40)
        if self.alerts.total > 0:
            lines.append(f"  Total: {self.alerts.total}")
            if self.alerts.info > 0:
                lines.append(f"    ℹ️ Info: {self.alerts.info}")
            if self.alerts.warning > 0:
                lines.append(f"    ⚠️ Warning: {self.alerts.warning}")
            if self.alerts.critical > 0:
                lines.append(f"    🚨 Critical: {self.alerts.critical}")
            if self.alerts.red_alert > 0:
                lines.append(f"    🔴 Red Alert: {self.alerts.red_alert}")
        else:
            lines.append("  ✅ No alerts")
        lines.append("")
        
        # Проблемные устройства
        if self.problematic_devices:
            lines.append("⚠️ PROBLEMATIC DEVICES")
            lines.append("-" * 40)
            for device_id in self.problematic_devices:
                lines.append(f"  • {device_id}")
            lines.append("")
        
        # Итог
        lines.append("=" * 60)
        if self.day_status == "NORMAL":
            lines.append("✅ Day completed normally")
        elif self.day_status == "ISSUES":
            lines.append("⚠️ Day completed with issues requiring attention")
        else:
            lines.append("🔴 Critical issues occurred during the day")
        
        return "\n".join(lines)
    
    def to_dict(self) -> Dict[str, Any]:
        """Конвертировать в словарь."""
        return {
            "report_date": self.report_date.isoformat(),
            "generated_at": self.generated_at.isoformat(),
            "morning_execution": self.morning_execution.to_dict() if self.morning_execution else None,
            "evening_execution": self.evening_execution.to_dict() if self.evening_execution else None,
            "monitoring_checks": self.monitoring_checks,
            "average_online_rate": self.average_online_rate,
            "min_online_rate": self.min_online_rate,
            "alerts": self.alerts.dict(),
            "problematic_devices": self.problematic_devices,
            "day_status": self.day_status
        }
    
    def to_json(self) -> str:
        """Конвертировать в JSON."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


class ReportGenerator:
    """
    Генератор отчётов.
    
    Создаёт ExecutionReport и DailyReport на основе данных
    из DeviceManager и MonitorService.
    """
    
    def __init__(
        self,
        reports_dir: str = "data/reports",
        device_manager: Optional[Any] = None,
        monitor_service: Optional[Any] = None
    ):
        """
        Инициализация генератора.
        
        Args:
            reports_dir: Директория для отчётов
            device_manager: DeviceManager instance
            monitor_service: MonitorService instance
        """
        self.reports_dir = Path(reports_dir)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        
        self._device_manager = device_manager
        self._monitor_service = monitor_service
        
        # Кэш для текущего дня
        self._today_executions: List[ExecutionReport] = []
        self._today_online_rates: List[float] = []
    
    def record_execution(self, report: ExecutionReport) -> None:
        """
        Записать отчёт о выполнении.
        
        Args:
            report: ExecutionReport
        """
        self._today_executions.append(report)
        
        # Сохраняем в файл
        self._save_execution_report(report)
        
        logger.info(
            "execution_report_recorded",
            action=report.action,
            success_rate=report.success_rate,
            status=report.status
        )
    
    def record_online_rate(self, rate: float) -> None:
        """
        Записать текущий онлайн рейт.
        
        Args:
            rate: Процент онлайн устройств
        """
        self._today_online_rates.append(rate)
    
    def _save_execution_report(self, report: ExecutionReport) -> None:
        """Сохранить отчёт в файл."""
        try:
            date_str = report.timestamp.strftime("%Y-%m-%d")
            time_str = report.timestamp.strftime("%H%M%S")
            
            filename = f"execution_{date_str}_{time_str}_{report.action}.txt"
            filepath = self.reports_dir / filename
            
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(report.to_text())
            
            # JSON версия
            json_filename = f"execution_{date_str}_{time_str}_{report.action}.json"
            json_filepath = self.reports_dir / json_filename
            
            with open(json_filepath, "w", encoding="utf-8") as f:
                json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
            
        except Exception as e:
            logger.error("execution_report_save_error", error=str(e))
    
    def generate_daily_report(
        self,
        report_date: Optional[date] = None
    ) -> DailyReport:
        """
        Сгенерировать дневной отчёт.
        
        Args:
            report_date: Дата отчёта (по умолчанию сегодня)
            
        Returns:
            DailyReport
        """
        if report_date is None:
            report_date = date.today()
        
        # Находим утреннее включение и вечернее выключение
        morning_exec = None
        evening_exec = None
        
        for exec_report in self._today_executions:
            if exec_report.timestamp.date() == report_date:
                if exec_report.action == "TURN_ON":
                    morning_exec = exec_report
                elif exec_report.action == "TURN_OFF":
                    evening_exec = exec_report
        
        # Расчёт статистики мониторинга
        online_rates = [r for r in self._today_online_rates if r is not None]
        avg_rate = sum(online_rates) / max(len(online_rates), 1) if online_rates else 1.0
        min_rate = min(online_rates) if online_rates else 1.0
        
        # Сбор проблемных устройств
        problematic = set()
        
        if morning_exec:
            for detail in morning_exec.device_details:
                if detail.status == "FAILED":
                    problematic.add(detail.device_id)
        
        if evening_exec:
            for detail in evening_exec.device_details:
                if detail.status == "FAILED":
                    problematic.add(detail.device_id)
        
        # Определение статуса дня
        day_status = "NORMAL"
        
        if len(problematic) > 0:
            day_status = "ISSUES"
        
        if min_rate < 0.5:
            day_status = "CRITICAL"
        
        # Алерты (mock, реальные данные из MonitorService)
        alerts = AlertSummary(
            total=len(problematic),
            warning=len(problematic)
        )
        
        report = DailyReport(
            report_date=report_date,
            generated_at=datetime.now(),
            morning_execution=morning_exec,
            evening_execution=evening_exec,
            monitoring_checks=len(online_rates),
            average_online_rate=avg_rate,
            min_online_rate=min_rate,
            alerts=alerts,
            problematic_devices=list(problematic),
            day_status=day_status
        )
        
        logger.info(
            "daily_report_generated",
            date=report_date.isoformat(),
            status=day_status,
            problematic_count=len(problematic)
        )
        
        return report
    
    def save_daily_report(self, report: DailyReport) -> Path:
        """
        Сохранить дневной отчёт.
        
        Args:
            report: DailyReport
            
        Returns:
            Путь к файлу
        """
        date_str = report.report_date.strftime("%Y-%m-%d")
        
        # Текстовый отчёт
        txt_path = self.reports_dir / f"daily_{date_str}.txt"
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(report.to_text())
        
        # JSON отчёт
        json_path = self.reports_dir / f"daily_{date_str}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            f.write(report.to_json())
        
        logger.info(
            "daily_report_saved",
            path=str(txt_path)
        )
        
        return txt_path
    
    def get_reports_for_period(
        self,
        start_date: date,
        end_date: date
    ) -> List[Dict[str, Any]]:
        """
        Получить отчёты за период.
        
        Args:
            start_date: Начальная дата
            end_date: Конечная дата
            
        Returns:
            Список отчётов
        """
        reports = []
        
        for json_file in self.reports_dir.glob("daily_*.json"):
            try:
                # Извлекаем дату из имени файла
                date_str = json_file.stem.replace("daily_", "")
                report_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                
                if start_date <= report_date <= end_date:
                    with open(json_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        reports.append(data)
            except Exception as e:
                logger.warning("report_load_error", file=str(json_file), error=str(e))
        
        return sorted(reports, key=lambda r: r.get("report_date", ""))
    
    def clear_day_cache(self) -> None:
        """Очистить кэш текущего дня."""
        self._today_executions.clear()
        self._today_online_rates.clear()


# Global instance
_report_generator: Optional[ReportGenerator] = None


def get_report_generator() -> ReportGenerator:
    """Получить глобальный генератор отчётов."""
    global _report_generator
    if _report_generator is None:
        _report_generator = ReportGenerator()
    return _report_generator


# Пример использования:
if __name__ == "__main__":
    from datetime import datetime
    
    # Создаём генератор
    generator = ReportGenerator(reports_dir="data/reports")
    
    # Создаём пример ExecutionReport
    execution = ExecutionReport(
        timestamp=datetime.now(),
        action="TURN_ON",
        trigger="scheduled",
        total_devices=45,
        successful=43,
        failed=2,
        skipped=0,
        devices_with_retries=5,
        total_retry_count=8,
        duration_seconds=125.5,
        status="PARTIAL",
        device_details=[
            DeviceExecutionDetail(
                device_id="optoma_2.64",
                device_name="Optoma 2.64",
                status="FAILED",
                attempts=3,
                duration_ms=90000,
                error="Connection timeout after 3 attempts"
            ),
            DeviceExecutionDetail(
                device_id="optoma_2.62",
                device_name="Optoma 2.62",
                status="SUCCESS",
                attempts=1,
                duration_ms=250
            ),
            DeviceExecutionDetail(
                device_id="barco_95",
                device_name="Barco 95",
                status="SUCCESS",
                attempts=2,
                duration_ms=35000,
                notes="Required retry"
            ),
            DeviceExecutionDetail(
                device_id="barco_97",
                device_name="Barco 97",
                status="FAILED",
                attempts=3,
                duration_ms=92000,
                error="Device not responding"
            ),
        ]
    )
    
    print("=== Execution Report ===")
    print(execution.to_text())
    
    # Записываем
    generator.record_execution(execution)
    generator.record_online_rate(0.95)
    generator.record_online_rate(0.93)
    generator.record_online_rate(0.96)
    
    # Генерируем дневной отчёт
    print("\n" + "=" * 60 + "\n")
    
    daily = generator.generate_daily_report()
    print("=== Daily Report ===")
    print(daily.to_text())
    
    # Сохраняем
    path = generator.save_daily_report(daily)
    print(f"\n✅ Report saved to: {path}")
