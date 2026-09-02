"""统一日志 Handler、格式和安全降级。"""

import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .sanitization import sanitize_log_value

LOGGER_NAME = "summary_video"
BEIJING_TIMEZONE = timezone(timedelta(hours=8))
DEFAULT_LOG_DIR = Path(__file__).parents[1] / "logs"
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 5
LOG_FIELDS = (
    "task_type",
    "task_id",
    "run_id",
    "partition",
    "duration_ms",
)


class JsonLineFormatter(logging.Formatter):
    def format(self, record):
        module = record.name.removeprefix(f"{LOGGER_NAME}.")
        payload = {
            "timestamp": datetime.fromtimestamp(
                record.created, BEIJING_TIMEZONE
            ).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "module": module,
            "event": sanitize_log_value(getattr(record, "event", "log_message")),
            "message": sanitize_log_value(record.getMessage()),
        }
        for field in LOG_FIELDS:
            payload[field] = sanitize_log_value(getattr(record, field, None))
        return json.dumps(payload, ensure_ascii=False)


def _project_logger():
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def shutdown_logging():
    logger = _project_logger()
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except (OSError, ValueError):
            pass


def configure_logging(
    component,
    log_dir=DEFAULT_LOG_DIR,
    max_bytes=DEFAULT_MAX_BYTES,
    backup_count=DEFAULT_BACKUP_COUNT,
    stream=None,
    console_enabled=True,
):
    shutdown_logging()
    logger = _project_logger()
    formatter = JsonLineFormatter()
    if console_enabled:
        console = logging.StreamHandler(stream if stream is not None else sys.stderr)
        console.setFormatter(formatter)
        logger.addHandler(console)
    file_enabled = False
    try:
        directory = Path(log_dir)
        directory.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            directory / f"{component}.log",
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        file_enabled = True
    except (OSError, ValueError):
        file_enabled = False
    return {"file_enabled": file_enabled}


def get_logger(module):
    return logging.getLogger(f"{LOGGER_NAME}.{module}")


def log_event(logger, level, event, message, **context):
    extra = {field: context.get(field) for field in LOG_FIELDS}
    extra["event"] = event
    try:
        logger.log(getattr(logging, str(level).upper()), message, extra=extra)
    except Exception:
        return False
    return True


__all__ = [
    "configure_logging",
    "get_logger",
    "log_event",
    "shutdown_logging",
]
