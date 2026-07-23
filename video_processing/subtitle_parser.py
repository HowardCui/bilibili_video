#!/usr/bin/env python 3.12
# -*- coding: utf-8 -*-
# time: 2026/07/23
# name: Haowen Cui

import json
import re
from pathlib import Path

TRANSCRIPT_DIR = Path("../data/transcripts")

def timestamp_to_seconds(timestamp: str):
    """
    将 SRT 时间转换为秒。
    例如：
    00:01:02,500 -> 62.5

    :param:SRT 格式的时间戳
    :return:秒
    """

    hours, minutes, seconds_part = timestamp.split(":")
    seconds, milliseconds = seconds_part.split(",")

    total_seconds = (int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(milliseconds) / 1000 )
    return round(total_seconds, 3)

def parse_srt(subtitle_path: str):
    """
    解析当前 yt-dlp 下载的标准 SRT 字幕。

    :param subtitle_path: SRT 字幕文件路径
    :return: 字幕片段列表
    """
    subtitle_path = Path(subtitle_path)

    if not subtitle_path.exists():
        raise FileNotFoundError(
            f"字幕文件不存在：{subtitle_path}"
        )

    content = subtitle_path.read_text(
        encoding="utf-8-sig"
    )

    # 统一换行格式
    content = content.replace("\r\n", "\n").strip()

    # 每个字幕段之间有一个空行
    blocks = content.split("\n\n")

    segments = []

    for block in blocks:
        lines = block.splitlines()

        # 当前文件每段固定为：
        # 序号
        # 时间
        # 字幕文字
        if len(lines) != 3:
            continue

        sequence = lines[0].strip()
        time_line = lines[1].strip()
        text = lines[2].strip()

        if not sequence.isdigit():
            continue

        if " --> " not in time_line:
            continue

        start_time, end_time = time_line.split(
            " --> ",
            maxsplit=1,
        )

        if not text:
            continue

        video_content =  {
                "index": int(sequence),
                "start": timestamp_to_seconds(
                    start_time
                ),
                "end": timestamp_to_seconds(
                    end_time
                ),
                "text": text,
            }

        segments.append(video_content)

    if not segments:
        raise ValueError( f"没有解析出有效字幕：{subtitle_path}")

    return segments

def get_video_info_from_filename(subtitle_path: str) :
    """
    从文件名中提取 BV 号和语言。

    文件名示例：
    BV1Ru6BBwEAn.ai-zh.srt
    """
    subtitle_path = Path(subtitle_path)

    filename_without_extension = subtitle_path.stem

    try:
        video_id, language = filename_without_extension.split(".")
    except ValueError as exc:
        raise ValueError("字幕文件名应为：BV号.语言.srt") from exc

    return video_id, language

def save_transcript(subtitle_path: str):
    """
    将 SRT 字幕转换成统一文字稿 JSON。

    :param subtitle_path: SRT 字幕路径
    :return: 生成的 JSON 文件路径
    """
    subtitle_path = Path(subtitle_path)

    video_id, language = get_video_info_from_filename(subtitle_path)

    segments = parse_srt(subtitle_path)

    words = []
    for segment in segments:
        words.append(segment["text"])

    content="\n".join(words)

    transcript = {
        "video_id": video_id,
        "source": "bilibili_ai_subtitle",
        "language": language,
        "segment_count": len(segments),
        "segments": segments,
        "text": content,
    }

    TRANSCRIPT_DIR.mkdir(parents=True,exist_ok=True,)

    output_path = (TRANSCRIPT_DIR / f"{video_id}.json")

    transcript_json = json.dumps(transcript,ensure_ascii=False,indent=2,)

    output_path.write_text(transcript_json, encoding="utf-8",)

    return output_path

def _test():
    '''
    测试
    :return:
    '''
    subtitle_path = Path("../data/subtitles/BV1Ru6BBwEAn.ai-zh.srt")
    save_transcript(subtitle_path)

if __name__ == '__main__':
    _test()
