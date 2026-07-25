#!/usr/bin/env python
# -*- coding: utf-8 -*-
# time: 2026/07/24
# name: Haowen Cui

def build_chunk(segments, chunk_index):
    """
    将多个字幕片段整理成一个文字稿分段。

    :param segments: 当前分段中的字幕片段
    :param chunk_index: 分段编号
    :return: 文字稿分段
    """
    text_list = []

    for segment in segments:
        text = segment.get("text", "")

        if text:
            text_list.append(text)

    chunk = {
        "chunk_index": chunk_index,
        "start": segments[0].get("start"),
        "end": segments[-1].get("end"),
        "segment_count": len(segments),
        "text": "\n".join(text_list),
    }

    return chunk


def split_transcript(segments, max_characters=6000):
    """
    根据文字数量划分文字稿。

    :param segments: transcript 中的字幕片段
    :param max_characters: 每个分段的最大字符数量
    :return: 分段列表
    """
    if not segments:
        raise ValueError("文字稿中没有 segments")

    chunks = []
    current_segments = []
    current_length = 0

    for segment in segments:
        text = segment.get("text", "")
        text_length = len(text)

        if (
            current_segments
            and current_length + text_length > max_characters
        ):
            chunk = build_chunk(
                current_segments,
                len(chunks) + 1,
            )

            chunks.append(chunk)

            current_segments = []
            current_length = 0

        current_segments.append(segment)
        current_length += text_length

    if current_segments:
        chunk = build_chunk(
            current_segments,
            len(chunks) + 1,
        )

        chunks.append(chunk)

    return chunks