"""Safe adapters for exposing summary work to the local web application."""

import re
from pathlib import Path

from summarization.errors import ModelUnavailableError
from video_processing.video_transcript_pipeline import (
    NoChineseSubtitleError,
    VideoUnavailableError,
)

_WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_WINDOWS_DRIVE_RELATIVE_PATH = re.compile(
    r"^[A-Za-z]:(?:[^\\/\s]+(?:[\\/][^\\/\s]*)+|[^\\/\s]*\.[^\\/\s]+)$"
)
_WINDOWS_ROOT_RELATIVE_PATH = re.compile(r"^\\")
_POSIX_PATH = re.compile(r"^(?:/|\\\\|\.{1,2}[\\/])")
_RELATIVE_ARTIFACT_PATH = re.compile(
    r"^(?:data|metadata|subtitles|transcripts|summaries)[\\/]",
    re.IGNORECASE,
)
_PATH_KEY_SUFFIXES = ("_path", "_file", "_directory", "_dir")
_NON_IDENTIFIER = re.compile(r"[^a-z0-9]+")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_TRACEBACK_TEXT = re.compile(
    r"(?:^|\n)Traceback \(most recent call last\):|"
    r"(?:^|\n)\s*File [\"'][^\n]+[\"'], line \d+",
    re.IGNORECASE,
)
_CREDENTIAL_VALUE = re.compile(
    r"\b(?:api[_ -]?key|authorization|cookie|access[_ -]?token|"
    r"refresh[_ -]?token|client[_ -]?secret|password|session)\b"
    r"\s*[:=]\s*(?:bearer\s+)?[^\s,;]+",
    re.IGNORECASE,
)
_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "cookie",
        "cookies",
        "set_cookie",
        "access_token",
        "refresh_token",
        "client_secret",
        "password",
        "credential",
        "credentials",
        "secret",
        "secrets",
        "headers",
        "traceback",
        "stack_trace",
        "exception",
        "error",
        "error_message",
        "diagnostic",
        "diagnostics",
        "internal_error",
        "provider_error",
        "provider_details",
        "provider_response",
        "provider_request",
        "raw_provider_response",
        "raw_provider_request",
        "raw_response",
        "raw_request",
        "raw_payload",
        "raw",
    }
)
_SENSITIVE_COMPACT_KEYS = frozenset(key.replace("_", "") for key in _SENSITIVE_KEYS)

_PUBLIC_ERRORS = {
    "NO_CHINESE_SUBTITLE": "No usable Chinese subtitles are available for this video.",
    "VIDEO_UNAVAILABLE": "This video is unavailable or cannot be accessed.",
    "MODEL_UNAVAILABLE": "The summary model is unavailable. Please try again later.",
    "SUMMARY_FAILED": "The video summary could not be completed.",
}


def public_error_from_exception(error: Exception) -> tuple[str, str]:
    """Return a stable, non-sensitive public error code and message."""
    if isinstance(error, NoChineseSubtitleError):
        code = "NO_CHINESE_SUBTITLE"
    elif isinstance(error, VideoUnavailableError):
        code = "VIDEO_UNAVAILABLE"
    elif isinstance(error, ModelUnavailableError):
        code = "MODEL_UNAVAILABLE"
    else:
        code = "SUMMARY_FAILED"
    return code, _PUBLIC_ERRORS[code]


def _is_path_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    normalized = key.lower()
    return normalized == "path" or normalized.endswith(_PATH_KEY_SUFFIXES)


def _normalized_key(key: object) -> str:
    if not isinstance(key, str):
        return ""
    with_boundaries = _CAMEL_BOUNDARY.sub("_", key)
    return _NON_IDENTIFIER.sub("_", with_boundaries.lower()).strip("_")


def _is_sensitive_key(key: object) -> bool:
    normalized = _normalized_key(key)
    if not normalized:
        return False
    compact = normalized.replace("_", "")
    if normalized in _SENSITIVE_KEYS or compact in _SENSITIVE_COMPACT_KEYS:
        return True
    return normalized.endswith(("_credential", "_credentials", "_secret"))


def _is_local_path(value: object) -> bool:
    if isinstance(value, Path):
        return True
    if not isinstance(value, str):
        return False
    return bool(
        _WINDOWS_PATH.match(value)
        or _WINDOWS_DRIVE_RELATIVE_PATH.match(value)
        or _WINDOWS_ROOT_RELATIVE_PATH.match(value)
        or _POSIX_PATH.match(value)
        or _RELATIVE_ARTIFACT_PATH.match(value)
    )


def _is_sensitive_string(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return bool(_TRACEBACK_TEXT.search(value) or _CREDENTIAL_VALUE.search(value))


def _public_value(value: object):
    if isinstance(value, dict):
        return {
            key: _public_value(item)
            for key, item in value.items()
            if not _is_path_key(key)
            and not _is_sensitive_key(key)
            and not _is_local_path(item)
            and not _is_sensitive_string(item)
        }
    if isinstance(value, (list, tuple)):
        return [
            item for item in (_public_value(item) for item in value) if item is not None
        ]
    if _is_local_path(value) or _is_sensitive_string(value):
        return None
    return value


def public_summary_result(result: dict) -> dict:
    """Copy JSON-safe public data while excluding secrets and diagnostics."""
    if not isinstance(result, dict):
        raise TypeError("result must be a dictionary")
    return _public_value(result)
