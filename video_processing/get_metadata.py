#!/usr/bin/env python 3.12
# -*- coding: utf-8 -*-
# time: 2026/07/21
# name: Haowen Cui

import json
from pathlib import Path
import os
import yt_dlp
from yt_dlp.networking.impersonate import ImpersonateTarget

BASE_DIR = Path(__file__).resolve().parent.parent
METADATA_DIR = BASE_DIR / "data" / "metadata"


def build_yt_dlp_options() -> dict:
    options = {
        "skip_download": True,
        "noplaylist": True,
        "verbose": True,
        "writesubtitles": True,
        "impersonate": ImpersonateTarget.from_str("firefox"),
    }

    cookie_file = os.getenv("BILIBILI_COOKIE_FILE")

    if cookie_file:
        options["cookiefile"] = cookie_file
    else:
        # 仅用于本地开发
        options["cookiesfrombrowser"] = ("firefox",)

    return options


def get_video_metadata(url: str, return_raw_info: bool = False):
    """
    获取 Bilibili 视频基础信息。
    当前阶段只读取信息，不下载视频和字幕。

    :param: Bilibili 视频链接
    :return: 包含标题、作者、简介、时长和字幕信息的字典
    """
    try:
        options=build_yt_dlp_options()

        with yt_dlp.YoutubeDL(options) as ydl:
            raw_info=ydl.extract_info(
                url,
                download=False,
            )
            info=ydl.sanitize_info(raw_info)
    except Exception as e:
        print(f"Error extracting metadata: {e}")
        return None

    subtitles = info.get("subtitles") or {}
    automatic_captions = info.get("automatic_captions") or {}

    metadata = {
        "video_id": info.get("id"),
        "title": info.get("title"),
        "description": info.get("description") or "",
        "uploader": info.get("uploader"),
        "uploader_id": info.get("uploader_id"),
        "duration": info.get("duration"),
        "duration_string": info.get("duration_string"),
        "upload_date": info.get("upload_date"),
        "view_count": info.get("view_count"),
        "like_count": info.get("like_count"),
        "comment_count": info.get("comment_count"),
        "tags": info.get("tags") or [],
        "thumbnail": info.get("thumbnail"),
        "webpage_url": info.get("webpage_url") or url,

        # 暂时只保存字幕语言名称
        "subtitle_languages": list(subtitles.keys()),
        "automatic_caption_languages": list(
            automatic_captions.keys()
        ),
    }
    if return_raw_info:
        return metadata, raw_info

    return metadata

def save_metadata(metadata: dict):
    """
    将视频信息保存到 data/metadata。

    :param: 视频数据
    :return: 存储路径
    """

    video_id = metadata.get("video_id")

    if not video_id:
        raise ValueError("没有获取到视频 ID")

    METADATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_path = METADATA_DIR / f"{video_id}.json"

    output_path.write_text(
        json.dumps(
            metadata,
            ensure_ascii=False,
            indent=2,
        ),
        encoding = "utf-8",
    )

    return output_path


def _test():
    '''
    测试
    :return: Bilibili 视频链接
    '''
    pathname = BASE_DIR / "sample.json"
    with open(pathname, 'r', encoding='utf-8') as f:
        data = json.load(f)
    url = data.get('url')
    return url

def _validation(pathname: str):
    '''
    验证
    :return: None
    '''
    with open(pathname, 'r', encoding='utf-8') as f:
        metadata = json.load(f)

    print("-" * 40)
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    print("-" * 40)

if __name__ == "__main__":
    url = _test()
    test_data = get_video_metadata(url)
    save_path = save_metadata(test_data)
    _validation(save_path)
