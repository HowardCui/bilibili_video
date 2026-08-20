#!/usr/bin/env python 3.12

"""Metadata, subtitle, and transcript processing for one Bilibili video."""

import json
import time
from pathlib import Path

from video_processing.danmaku_parser import (
    CACHE_MAX_AGE_HOURS,
    DANMAKU_CACHE_DIR,
    build_word_cloud,
    load_word_cloud_cache,
    parse_danmaku_xml,
    save_word_cloud_cache,
    unavailable_word_cloud,
)
from video_processing.get_danmaku import download_danmaku
from video_processing.get_metadata import get_video_metadata, save_metadata
from video_processing.get_subtitles import (
    download_subtitle,
    select_subtitle_language,
)
from video_processing.subtitle_parser import save_transcript

BASE_DIR = Path(__file__).resolve().parent.parent


class VideoUnavailableError(RuntimeError):
    """Raised when video metadata cannot be obtained."""


class NoChineseSubtitleError(RuntimeError):
    """Raised when a video has no supported Chinese subtitle."""


def collect_danmaku_word_cloud(
    url,
    video_id,
    raw_info,
    cache_dir=DANMAKU_CACHE_DIR,
):
    """Return cached or freshly calculated word-cloud data without failing summary."""
    cache_path = Path(cache_dir) / f"{video_id}.json"
    cached = load_word_cloud_cache(
        cache_path,
        max_age_hours=CACHE_MAX_AGE_HOURS,
    )
    if cached is not None:
        return cached

    subtitles = raw_info.get("subtitles") if isinstance(raw_info, dict) else None
    if not isinstance(subtitles, dict) or "danmaku" not in subtitles:
        return unavailable_word_cloud(video_id, "NO_DANMAKU")

    try:
        danmaku_path = download_danmaku(url, raw_info=raw_info)
    except Exception:
        return unavailable_word_cloud(video_id, "DOWNLOAD_FAILED")

    try:
        comments = parse_danmaku_xml(danmaku_path)
        result = build_word_cloud(video_id, comments)
        save_word_cloud_cache(result, cache_path)
        return result
    except Exception:
        return unavailable_word_cloud(video_id, "PARSE_FAILED")


def _report_stage(progress_callback, stage: str) -> None:
    if progress_callback is not None:
        progress_callback(stage)


def process_video(url: str, progress_callback=None):
    """Create metadata, subtitle, and transcript artifacts for ``url``."""
    start_time = time.perf_counter()

    print("Retrieving video metadata")
    _report_stage(progress_callback, "METADATA")
    extraction_result = get_video_metadata(url, return_raw_info=True)
    if extraction_result is None:
        raise VideoUnavailableError("video metadata could not be retrieved")

    metadata, raw_info = extraction_result
    metadata_path = save_metadata(metadata)

    _report_stage(progress_callback, "SUBTITLE")
    subtitle_language = select_subtitle_language(
        metadata.get("subtitle_languages", [])
    )
    if subtitle_language is None:
        raise NoChineseSubtitleError("no supported Chinese subtitle is available")

    print("Retrieving video subtitle")
    try:
        subtitle_path = download_subtitle(
            url=url,
            language=subtitle_language,
            raw_info=raw_info,
        )
    except Exception as error:
        raise NoChineseSubtitleError(
            "the advertised Chinese subtitle could not be retrieved"
        ) from error

    _report_stage(progress_callback, "TRANSCRIPT")
    transcript_path = save_transcript(subtitle_path=subtitle_path)
    danmaku_word_cloud = collect_danmaku_word_cloud(
        url,
        metadata["video_id"],
        raw_info,
    )

    elapsed_time = time.perf_counter() - start_time
    print(f"Video processing completed in {elapsed_time:.2f} seconds")

    return {
        "video_id": metadata["video_id"],
        "metadata_path": metadata_path,
        "subtitle_path": subtitle_path,
        "transcript_path": transcript_path,
        "danmaku_word_cloud": danmaku_word_cloud,
    }


if __name__ == "__main__":
    pathname = BASE_DIR / "sample.json"
    with pathname.open(encoding="utf-8") as file:
        data = json.load(file)
    process_video(data.get("url"))
