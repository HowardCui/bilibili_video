#!/usr/bin/env python 3.12
# -*- coding: utf-8 -*-

"""Metadata, subtitle, and transcript processing for one Bilibili video."""

import json
import time
from pathlib import Path

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

    elapsed_time = time.perf_counter() - start_time
    print(f"Video processing completed in {elapsed_time:.2f} seconds")

    return {
        "video_id": metadata["video_id"],
        "metadata_path": metadata_path,
        "subtitle_path": subtitle_path,
        "transcript_path": transcript_path,
    }


if __name__ == "__main__":
    pathname = BASE_DIR / "sample.json"
    with pathname.open(encoding="utf-8") as file:
        data = json.load(file)
    process_video(data.get("url"))
