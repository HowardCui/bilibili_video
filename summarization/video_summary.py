#!/usr/bin/env python 3.12
# time: 2026/07/24
# name: Haowen Cui

import json
import time
from pathlib import Path

from summarization.errors import ModelUnavailableError, SummaryFailedError
from summarization.summarizer import (
    load_transcript,
    merge_chunk_summaries,
    save_summary,
    summarize_chunks,
)
from summarization.transcript_splitter import split_transcript
from video_processing.video_transcript_pipeline import process_video

BASE_DIR = Path(__file__).resolve().parent.parent


def _report_stage(progress_callback, stage: str) -> None:
    if progress_callback is not None:
        progress_callback(stage)


def _is_model_unavailable(error: Exception) -> bool:
    if isinstance(error, (ConnectionError, TimeoutError)):
        return True

    message = str(error).lower()
    model_markers = (
        "api key",
        "authentication",
        "authorization",
        "connection",
        "credential",
        "dashscope",
        "model unavailable",
        "openai",
        "rate limit",
        "timeout",
    )
    return any(marker in message for marker in model_markers)


def _summary_error(error: Exception) -> RuntimeError:
    if _is_model_unavailable(error):
        return ModelUnavailableError("summary model is unavailable")
    return SummaryFailedError("summary processing failed")


def summarize_bilibili_video(
    url: str,
    max_characters: int = 3000,
    max_workers: int = 4,
    progress_callback=None,
):
    """
    执行普通用户使用的 Bilibili 视频总结完整链路。

    :param url: Bilibili 视频链接
    :param max_characters: 单个字幕分段的最大字符数
    :param max_workers: 分段总结的最大并发数
    :return: 视频处理产物、结构化总结和运行信息
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Bilibili 视频链接不能为空")

    if max_characters < 1:
        raise ValueError("max_characters 必须大于等于 1")

    if max_workers < 1:
        raise ValueError("max_workers 必须大于等于 1")

    start_time = time.perf_counter()

    processing_result = process_video(
        url.strip(),
        progress_callback=progress_callback,
    )
    try:
        transcript = load_transcript(
            processing_result["transcript_path"]
        )
        _report_stage(progress_callback, "SPLIT")
        chunks = split_transcript(
            transcript["segments"],
            max_characters=max_characters,
        )
        _report_stage(progress_callback, "SUMMARIZE_CHUNKS")
        chunk_summaries = summarize_chunks(
            chunks,
            max_workers=max_workers,
        )
        _report_stage(progress_callback, "MERGE")
        summary = merge_chunk_summaries(chunk_summaries)
        summary_path = save_summary(
            summary,
            processing_result["video_id"],
        )
    except (ModelUnavailableError, SummaryFailedError):
        raise
    except Exception as error:
        raise _summary_error(error) from error

    elapsed_seconds = time.perf_counter() - start_time

    return {
        "video_id": processing_result["video_id"],
        "metadata_path": str(processing_result["metadata_path"]),
        "subtitle_path": str(processing_result["subtitle_path"]),
        "transcript_path": str(processing_result["transcript_path"]),
        "summary_path": str(summary_path),
        "chunk_count": len(chunks),
        "summary": summary,
        "danmaku_word_cloud": processing_result.get("danmaku_word_cloud"),
        "elapsed_seconds": round(elapsed_seconds, 2),
    }


def main():
    sample_path = BASE_DIR / "sample.json"
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    result = summarize_bilibili_video(sample.get("url", ""))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
