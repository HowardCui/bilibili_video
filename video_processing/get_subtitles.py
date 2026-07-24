#!/usr/bin/env python 3.12
# -*- coding: utf-8 -*-
# time: 2026/07/23
# name: Haowen Cui

import json
from pathlib import Path

import yt_dlp

from video_processing.get_metadata import get_video_metadata

SUBTITLE_DIR = Path("../data/subtitles")

SUBTITLE_PRIORITY = [
    "ai-zh",
    "zh-CN",
    "zh-Hans",
    "zh",
]

BASE_SUBTITLE_OPTIONS = {
    "skip_download": True,
    "noplaylist": True,
    "cookiesfrombrowser": ("firefox",),
    "verbose": True,
}

def select_subtitle_language(subtitle_languages: list,):
    """
    根据优先级选择中文字幕。

    :param subtitle_languages: 可用字幕语言列表
    :return: 选中的字幕语言；没有可用字幕时返回 None
    """
    for language in SUBTITLE_PRIORITY:
        if language in subtitle_languages:
            return language
    return None

def download_subtitle(url: str, language: str, raw_info=None):
    """
    下载指定语言的字幕。

    :param url: Bilibili 视频链接
    :param language: 字幕语言，例如 ai-zh
    :return: 下载后的字幕文件路径
    """
    SUBTITLE_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    subtitle_options = BASE_SUBTITLE_OPTIONS.copy()

    subtitle_options.update(
        {
            "writesubtitles": True,
            "subtitleslangs": [language],
            "subtitlesformat": "best",
            "outtmpl": str(
                SUBTITLE_DIR / "%(id)s.%(ext)s"
            ),
        }
    )

    with yt_dlp.YoutubeDL(subtitle_options) as ydl:
        if raw_info is None:
            processed_info = ydl.extract_info(
                url,
                download=True,
            )
        else:
            # 重新使用raw_info避免重新请求
            processed_info = ydl.process_ie_result(
                raw_info,
                download=True,
            )

    video_id = processed_info.get("id")

    if not video_id:
        raise ValueError("没有获取到视频 ID")

    matching_files = list(
        SUBTITLE_DIR.glob(
            f"{video_id}.{language}.*"
        )
    )

    if not matching_files:
        raise FileNotFoundError(
            f"字幕下载完成，但没有找到对应文件："
            f"{video_id}.{language}.*"
        )

    return matching_files[0]

def _test():
    '''
    测试
    :return: Bilibili 视频链接
    '''
    pathname ='../sample.json'
    with open(pathname, 'r', encoding='utf-8') as f:
        data = json.load(f)
    url = data.get('url')
    return url

def _validation(metadata: dict):
    '''
    验证
    :return: None
    '''
    subtitle_languages=metadata.get(
        "subtitle_languages",
        [],
    )

    print(
        f"可用字幕：{subtitle_languages}"
    )

    selected_language=(
        select_subtitle_language(
            subtitle_languages
        )
    )

    print(
        f"选择字幕：{selected_language}"
    )

    subtitle_path=download_subtitle(
        url=url,
        language=selected_language,
    )

    print(
        f"字幕已保存到：{subtitle_path}"
    )

if __name__ == "__main__":
    url=_test()
    metadata=get_video_metadata(url)
    _validation(metadata)
