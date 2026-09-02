#!/usr/bin/env python 3.12

"""Download Bilibili danmaku through yt-dlp's subtitle support."""

import os
from datetime import UTC, datetime
from pathlib import Path

import yt_dlp

from app_logging import get_logger, log_event

BASE_DIR = Path(__file__).resolve().parent.parent
DANMAKU_DIR = BASE_DIR / "data" / "danmaku"
DEFAULT_COOKIE_FILE = BASE_DIR / ".secrets" / "bilibili_cookies.txt"
DANMAKU_CACHE_MAX_AGE_HOURS = 168
LOGGER = get_logger("danmaku.download")


def _cookie_file():
    configured = os.getenv("BILIBILI_COOKIE_FILE")
    path = Path(configured) if configured else DEFAULT_COOKIE_FILE
    return path if path.is_file() else None


def build_danmaku_options(output_dir=DANMAKU_DIR, cookie_file=None) -> dict:
    """Build yt-dlp options for the XML ``danmaku`` subtitle track."""
    output_path = Path(output_dir)
    options = {
        "skip_download": True,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "writesubtitles": True,
        "subtitleslangs": ["danmaku"],
        "subtitlesformat": "xml",
        "overwrites": True,
        "outtmpl": str(output_path / "%(id)s.%(ext)s"),
    }
    if cookie_file is not None:
        options["cookiefile"] = str(cookie_file)
    return options


def _find_downloaded_file(output_dir, video_id, max_age_hours=None):
    matches = sorted(Path(output_dir).glob(f"{video_id}.danmaku*.xml"))
    if not matches:
        return None
    path = matches[0]
    if max_age_hours is not None:
        modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        age_hours = (datetime.now(UTC) - modified_at).total_seconds() / 3600
        if age_hours > max_age_hours:
            return None
    return path


def _download(url, raw_info, output_dir, cookie_file=None):
    options = build_danmaku_options(output_dir, cookie_file=cookie_file)
    with yt_dlp.YoutubeDL(options) as ydl:
        if raw_info is None:
            processed_info = ydl.extract_info(url, download=True)
        else:
            processed_info = ydl.process_ie_result(raw_info, download=True)

    video_id = processed_info.get("id") if isinstance(processed_info, dict) else None
    if not video_id:
        raise ValueError("没有获取到视频 ID")
    output_path = _find_downloaded_file(output_dir, video_id)
    if output_path is None:
        raise FileNotFoundError(f"弹幕下载完成，但没有找到 {video_id}.danmaku.xml")
    return output_path


def download_danmaku(
    url: str,
    raw_info=None,
    output_dir=DANMAKU_DIR,
    max_age_hours=DANMAKU_CACHE_MAX_AGE_HOURS,
):
    """Download one video's danmaku XML, anonymously before Cookie fallback."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Bilibili 视频链接不能为空")
    if (
        not isinstance(max_age_hours, (int, float))
        or isinstance(max_age_hours, bool)
        or max_age_hours <= 0
    ):
        raise ValueError("max_age_hours 必须大于 0")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    video_id = raw_info.get("id") if isinstance(raw_info, dict) else None
    if video_id:
        cached = _find_downloaded_file(
            output_dir,
            video_id,
            max_age_hours=max_age_hours,
        )
        if cached is not None:
            log_event(
                LOGGER,
                "INFO",
                "danmaku_cache_hit",
                "复用有效弹幕缓存",
                task_type="danmaku",
                task_id=video_id,
            )
            return cached

    try:
        result = _download(url.strip(), raw_info, output_dir)
        log_event(
            LOGGER,
            "INFO",
            "danmaku_download_succeeded",
            "匿名弹幕下载完成",
            task_type="danmaku",
            task_id=video_id,
        )
        return result
    except Exception as anonymous_error:
        log_event(
            LOGGER,
            "WARNING",
            "danmaku_anonymous_failed",
            "匿名弹幕下载失败，检查 Cookie 后备",
            task_type="danmaku",
            task_id=video_id,
        )
        cookie_file = _cookie_file()
        if cookie_file is None:
            raise anonymous_error
        result = _download(
            url.strip(),
            raw_info,
            output_dir,
            cookie_file=cookie_file,
        )
        log_event(
            LOGGER,
            "INFO",
            "danmaku_cookie_succeeded",
            "Cookie 后备弹幕下载完成",
            task_type="danmaku",
            task_id=video_id,
        )
        return result


__all__ = ["build_danmaku_options", "download_danmaku"]
