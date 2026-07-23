#!/usr/bin/env python 3.12
# -*- coding: utf-8 -*-
# time: 2026/07/23
# name: Haowen Cui

from get_metadata import get_video_metadata, save_metadata
from get_subtitles import select_subtitle_language,download_subtitle
from subtitle_parser import save_transcript
import json

def process_video(url: str):
    """
    执行单个 Bilibili 视频的文字稿处理流程。

    :param url: Bilibili 视频链接
    :return: 各阶段生成的文件路径
    """
    metadata = get_video_metadata(url)

    if metadata is None:
        raise RuntimeError("视频元数据获取失败")

    metadata_path = save_metadata(metadata)

    subtitle_language = select_subtitle_language(
        metadata.get("subtitle_languages", [])
    )

    if subtitle_language is None:
        raise RuntimeError("该视频没有可用的中文字幕")

    subtitle_path = download_subtitle(
        url=url,
        language=subtitle_language,
    )

    transcript_path = save_transcript(
        subtitle_path=subtitle_path,
    )

    return {
        "video_id": metadata["video_id"],
        "metadata_path": metadata_path,
        "subtitle_path": subtitle_path,
        "transcript_path": transcript_path,
    }

if __name__ == "__main__":
    pathname='../sample.json'
    with open(pathname, 'r', encoding='utf-8') as f:
        data=json.load(f)
    url=data.get('url')
    process_video(url)
