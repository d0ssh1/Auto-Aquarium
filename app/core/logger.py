"""
Structured logging setup using structlog.
"""

import logging
import sys
from pathlib import Path
from datetime import datetime

import structlog


def setup_logging(log_level: str = "INFO") -> None:
    """Configure structured logging."""
    
    # Ensure logs directory exists
    Path("logs").mkdir(exist_ok=True)
    
    # Configure structlog
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(ensure_ascii=False)
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    
    # Configure standard logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper(), logging.INFO),
    )


class ActionLogger:
    """Logger for device actions with file output."""
    
    def __init__(self, log_file: str = "logs/actions.jsonl"):
        self.log_file = Path(log_file)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self.logger = structlog.get_logger("actions")
    
    def log_action(
        self,
        device_id: str,
        device_name: str,
        action: str,
        trigger: str,
        success: bool,
        attempt: int = 1,
        duration_ms: int = 0,
        error: str = None,
        details: dict = None
    ) -> None:
        """Log a device action to both console and file."""
        
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "level": "INFO" if success else "ERROR",
            "event": "device_action",
            "device_id": device_id,
            "device_name": device_name,
            "action": action,
            "trigger": trigger,
            "attempt": attempt,
            "success": success,
            "duration_ms": duration_ms,
        }
        
        if error:
            log_entry["error"] = error
        
        if details:
            log_entry["details"] = details
        
        # Log to console via structlog
        if success:
            self.logger.info("device_action", **log_entry)
        else:
            self.logger.error("device_action", **log_entry)
        
        # Append to JSONL file
        import json
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")


# Global action logger instance
action_logger = ActionLogger()
