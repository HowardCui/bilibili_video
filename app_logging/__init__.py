"""项目统一结构化日志入口。"""

from .config import configure_logging, get_logger, log_event, shutdown_logging
from .sanitization import sanitize_log_value

__all__ = [
    "configure_logging",
    "get_logger",
    "log_event",
    "sanitize_log_value",
    "shutdown_logging",
]
