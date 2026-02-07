# 🏗️ Архитектура системы Ocean Aquarium Control

## Общий обзор

```
┌─────────────────────────────────────────────────────────────────┐
│                          WEB BROWSER                             │
│                    http://localhost:8000                         │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTP
┌────────────────────────────▼────────────────────────────────────┐
│                        FASTAPI APP                               │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                    REST API Endpoints                     │   │
│  │  /api/devices  /api/schedule  /api/logs  /api/alerts     │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────┬─────────────────┬─────────────────┬───────────────────────┘
      │                 │                 │
┌─────▼─────┐   ┌───────▼──────┐   ┌──────▼──────┐
│ Scheduler │   │Device Manager│   │   Monitor   │
│  Service  │   │              │   │   Service   │
└─────┬─────┘   └───────┬──────┘   └──────┬──────┘
      │                 │                 │
      │          ┌──────▼──────┐          │
      └─────────►│   Registry  │◄─────────┘
                 └──────┬──────┘
                        │
    ┌───────────────────┼───────────────────┐
    │                   │                   │
┌───▼────┐      ┌───────▼──────┐     ┌──────▼─────┐
│ Telnet │      │    Barco     │     │   Device   │
│ Client │      │   Client     │     │  Monitor   │
│(Optoma)│      │ (JSON-RPC)   │     │  (Ping)    │
└───┬────┘      └──────┬───────┘     └─────┬──────┘
    │                  │                   │
    └──────────────────┼───────────────────┘
                       ▼
           ┌───────────────────────┐
           │   NETWORK DEVICES     │
           │  Projectors, Cubes,   │
           │    Exposition PCs     │
           └───────────────────────┘
```

## Модули

| Модуль | Файл | Описание |
|--------|------|----------|
| **Main App** | `main.py` | FastAPI приложение, API endpoints, lifespan |
| **Device Registry** | `core/device_registry.py` | Реестр устройств и групп |
| **Logger Service** | `core/logger_service.py` | Структурированное JSON логирование |
| **Telnet Client** | `protocols/telnet_client.py` | Управление Optoma проекторами |
| **Barco Client** | `protocols/barco_client.py` | JSON-RPC для Barco проекторов |
| **Device Monitor** | `protocols/device_monitor.py` | Ping/TCP/HTTP проверки |
| **Scheduler Service** | `services/scheduler_service.py` | APScheduler + SQLite |
| **Device Manager** | `services/device_manager.py` | Параллельное управление с retry |
| **Monitor Service** | `services/monitor_service.py` | Мониторинг состояния |
| **Reports** | `services/reports.py` | Генерация отчётов |

## API Reference

### Health Check
```http
GET /api/health

Response:
{
  "status": "running",
  "devices_total": 40,
  "devices_online": 38,
  "success_rate": 0.95,
  "scheduler_running": true
}
```

### Devices
```http
GET /api/devices
POST /api/devices/{device_id}/on
POST /api/devices/{device_id}/off
POST /api/devices/all/on
POST /api/devices/all/off
```

### Groups
```http
GET /api/groups
GET /api/groups/status
POST /api/groups/{group_id}/on
POST /api/groups/{group_id}/off
```

### Schedule
```http
GET /api/schedule
POST /api/schedule
GET /api/schedule/jobs
POST /api/schedule/jobs/{job_id}/trigger
```

### Logs & Alerts
```http
GET /api/logs?date=2026-02-07&level=ERROR&page=1
GET /api/alerts?hours=24
GET /api/logs/export
```

## Database Schema

### SQLite: scheduler.db
```sql
-- APScheduler jobs table (auto-managed)
apscheduler_jobs (
    id TEXT PRIMARY KEY,
    next_run_time REAL,
    job_state BLOB
)
```

### JSON Files

**logs/actions.jsonl** (JSON Lines):
```json
{"timestamp": "...", "device_id": "...", "action": "TURN_ON", "success": true, ...}
```

**data/reports/YYYY-MM-DD.json**:
```json
{"date": "2026-02-07", "executions": [...], "monitoring": {...}, "alerts": [...]}
```

## Error Handling

### Retry Policy
```python
RetryPolicy(
    max_attempts=3,
    base_interval_sec=30,
    backoff_multiplier=2.0
)
```

### Alert Levels
| Level | Trigger |
|-------|---------|
| INFO | Device recovered |
| WARNING | Device offline, single failure |
| CRITICAL | Multiple devices down |
| RED_ALERT | >20% devices offline |

### Graceful Degradation
- Отдельные сбои устройств не блокируют остальные
- Параллельное выполнение с ограничением (semaphore)
- Таймауты на все сетевые операции

## Data Flow

### Device Turn On Flow
```
1. API Request → POST /api/devices/{id}/on
2. DeviceManager.turn_on_device(id)
3. Registry.get_device(id) → Device info
4. Select client by device_type
5. TelnetClient.power_on(ip, port) OR BarcoClient.power_on(ip, port)
6. Retry loop with backoff
7. Log action to actions.jsonl
8. Return DeviceActionResponse
```

### Scheduled Execution Flow
```
1. APScheduler trigger at 09:00
2. SchedulerService → turn_on_callback()
3. DeviceManager.turn_on_all(parallel=True)
4. Parallel execution (max 10 concurrent)
5. Collect results → ExecutionReport
6. ReportGenerator.record_execution(report)
7. Log all actions
```
