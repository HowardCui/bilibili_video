"""日志字段的保守脱敏规则。"""

import re

_HEADER_SECRET = re.compile(r"(?i)\b(cookie|authorization)\s*:\s*[^\r\n]+")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(cookie|authorization|api[_-]?key|access[_-]?key|"
    r"sessdata|bili_jct|w_rid)\b\s*[:=]\s*[^\s,;]+"
)
_USER_PATH = re.compile(r"(?i)\b[A-Z]:[\\/]Users[\\/][^\\/\s]+")
_SECRET_PATH = re.compile(r"(?i)(?:[^\s]*[\\/])?\.secrets[\\/][^\s]+")


def _sanitize_text(value):
    text = str(value)
    text = _HEADER_SECRET.sub(
        lambda match: f"{match.group(1)}: [REDACTED]", text
    )
    text = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    text = _SECRET_PATH.sub("[REDACTED_PATH]", text)
    return _USER_PATH.sub("[USER_HOME]", text)


def sanitize_log_value(value):
    if isinstance(value, dict):
        return {str(key): sanitize_log_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [sanitize_log_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _sanitize_text(value)


__all__ = ["sanitize_log_value"]
