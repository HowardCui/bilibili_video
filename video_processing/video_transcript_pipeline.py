#!/usr/bin/env python 3.12
# -*- coding: utf-8 -*-
# time: 2026/07/23
# name: Haowen Cui

from get_metadata import get_video_metadata, save_metadata
from get_subtitles import select_subtitle_language,download_subtitle
from subtitle_parser import save_transcript
import json
import time

def process_video(url: str):
    """
    执行单个 Bilibili 视频的文字稿处理流程。

    :param url: Bilibili 视频链接
    :return: 各阶段生成的文件路径
    """
    start_time=time.perf_counter()
    print("正在获取视频元数据")
    extraction_result = get_video_metadata(
        url,
        return_raw_info=True,
    )
    if extraction_result is None:
        raise RuntimeError("视频元数据获取失败")

    metadata, raw_info = extraction_result
    metadata_path = save_metadata(metadata)

    subtitle_language = select_subtitle_language(
        metadata.get("subtitle_languages", [])
    )

    if subtitle_language is None:
        raise RuntimeError("该视频没有可用的中文字幕")
    #后续可能会添加音频转化方案获取字幕

    print("正在获取视频字幕")
    subtitle_path = download_subtitle(
        url=url,
        language=subtitle_language,
        raw_info=raw_info,
    )

    transcript_path = save_transcript(subtitle_path=subtitle_path)

    end_time=time.perf_counter()
    elapsed_time=end_time - start_time

    print(f"获取完整视频信息耗时：{elapsed_time:.2f} 秒")

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
