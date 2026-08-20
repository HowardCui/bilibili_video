"""Danmaku download, parsing, caching, and word-cloud tests."""

import os
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from video_processing import danmaku_parser, get_danmaku, video_transcript_pipeline
from web_app.summary.ui import render_danmaku_word_cloud, summary_result_sections


def test_parse_danmaku_xml_keeps_public_fields_and_respects_limit(tmp_path):
    xml_path = tmp_path / "BV1TEST.danmaku.xml"
    xml_path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<i><d p="1.5,1,25,16777215,0,0,hash,1">太好看了</d>'
        '<d p="2.0,5,18,65280,0,0,hash,2">知识 很有用</d>'
        '<d p="3.0,1,25,1,0,0,hash,3">不会被读取</d></i>',
        encoding="utf-8",
    )

    comments = danmaku_parser.parse_danmaku_xml(xml_path, max_comments=2)

    assert comments == [
        {"time": 1.5, "mode": 1, "font_size": 25, "color": 16777215,
         "text": "太好看了"},
        {"time": 2.0, "mode": 5, "font_size": 18, "color": 65280,
         "text": "知识 很有用"},
    ]


def test_build_word_cloud_filters_noise_duplicates_and_stop_words():
    comments = [
        {"text": "知识 知识 视频 真的 有用"},
        {"text": "知识 知识 视频 真的 有用"},
        {"text": "https://example.com BV1ABC 12345 !!!"},
        {"text": "分析 视频 清楚"},
    ]

    result = danmaku_parser.build_word_cloud(
        "BV1TEST",
        comments,
        tokenizer=lambda text: text.split(),
        stop_words={"真的"},
        max_words=10,
    )

    assert result["status"] == "AVAILABLE"
    assert result["total_comments"] == 4
    assert result["used_comments"] == 2
    assert result["words"] == [
        {"text": "视频", "count": 2},
        {"text": "知识", "count": 1},
        {"text": "有用", "count": 1},
        {"text": "分析", "count": 1},
        {"text": "清楚", "count": 1},
    ]


def test_word_cloud_cache_round_trip_does_not_store_comment_identity(tmp_path):
    cache_path = tmp_path / "BV1TEST.json"
    payload = {
        "video_id": "BV1TEST",
        "status": "AVAILABLE",
        "collected_at": "2026-08-20T00:00:00+00:00",
        "total_comments": 8,
        "used_comments": 6,
        "words": [{"text": "知识", "count": 3}],
    }

    danmaku_parser.save_word_cloud_cache(payload, cache_path)
    loaded = danmaku_parser.load_word_cloud_cache(cache_path)

    assert loaded == payload
    assert "user" not in cache_path.read_text(encoding="utf-8").lower()
    assert "hash" not in cache_path.read_text(encoding="utf-8").lower()


def test_expired_word_cloud_cache_is_not_reused(tmp_path):
    cache_path = tmp_path / "BV1TEST.json"
    stale_time = datetime.now(UTC) - timedelta(hours=8)
    danmaku_parser.save_word_cloud_cache(
        {
            "video_id": "BV1TEST",
            "status": "AVAILABLE",
            "collected_at": stale_time.isoformat(),
            "total_comments": 1,
            "used_comments": 1,
            "words": [{"text": "知识", "count": 1}],
        },
        cache_path,
    )

    assert danmaku_parser.load_word_cloud_cache(
        cache_path,
        max_age_hours=6,
    ) is None


def test_download_danmaku_reuses_raw_info_and_existing_cache(tmp_path, monkeypatch):
    output_dir = tmp_path / "danmaku"
    processed = {"id": "BV1TEST"}
    ydl = MagicMock()
    ydl.__enter__.return_value = ydl
    ydl.process_ie_result.return_value = processed

    def create_ydl(options):
        assert options["subtitleslangs"] == ["danmaku"]
        assert options["subtitlesformat"] == "xml"
        assert options["overwrites"] is True
        output = output_dir / "BV1TEST.danmaku.xml"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("<i></i>", encoding="utf-8")
        return ydl

    monkeypatch.setattr(get_danmaku.yt_dlp, "YoutubeDL", create_ydl)

    first = get_danmaku.download_danmaku(
        "https://www.bilibili.com/video/BV1TEST",
        raw_info={"id": "BV1TEST"},
        output_dir=output_dir,
    )
    second = get_danmaku.download_danmaku(
        "https://www.bilibili.com/video/BV1TEST",
        raw_info={"id": "BV1TEST"},
        output_dir=output_dir,
    )

    assert first == output_dir / "BV1TEST.danmaku.xml"
    assert second == first
    assert ydl.process_ie_result.call_count == 1
    assert ydl.extract_info.call_count == 0


def test_download_danmaku_refreshes_expired_xml_cache(tmp_path, monkeypatch):
    output_dir = tmp_path / "danmaku"
    output_dir.mkdir()
    cached = output_dir / "BV1TEST.danmaku.xml"
    cached.write_text("<i><d>old</d></i>", encoding="utf-8")
    old_timestamp = time.time() - 8 * 3600
    os.utime(cached, (old_timestamp, old_timestamp))
    ydl = MagicMock()
    ydl.__enter__.return_value = ydl
    ydl.process_ie_result.return_value = {"id": "BV1TEST"}

    def create_ydl(options):
        cached.write_text("<i><d>fresh</d></i>", encoding="utf-8")
        return ydl

    monkeypatch.setattr(get_danmaku.yt_dlp, "YoutubeDL", create_ydl)

    result = get_danmaku.download_danmaku(
        "https://www.bilibili.com/video/BV1TEST",
        raw_info={"id": "BV1TEST"},
        output_dir=output_dir,
        max_age_hours=6,
    )

    assert result == cached
    assert "fresh" in cached.read_text(encoding="utf-8")
    assert ydl.process_ie_result.call_count == 1


def test_unavailable_word_cloud_is_a_public_nonfatal_result():
    result = danmaku_parser.unavailable_word_cloud(
        "BV1TEST",
        "DOWNLOAD_FAILED",
    )

    assert result == {
        "video_id": "BV1TEST",
        "status": "UNAVAILABLE",
        "reason": "DOWNLOAD_FAILED",
        "total_comments": 0,
        "used_comments": 0,
        "words": [],
    }


def test_pipeline_danmaku_failure_returns_nonfatal_public_state(monkeypatch, tmp_path):
    monkeypatch.setattr(
        video_transcript_pipeline,
        "download_danmaku",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("secret")),
    )

    result = video_transcript_pipeline.collect_danmaku_word_cloud(
        "https://www.bilibili.com/video/BV1TEST",
        "BV1TEST",
        {
            "id": "BV1TEST",
            "subtitles": {"danmaku": [{"ext": "xml"}]},
        },
        cache_dir=tmp_path,
    )

    assert result == {
        "video_id": "BV1TEST",
        "status": "UNAVAILABLE",
        "reason": "DOWNLOAD_FAILED",
        "total_comments": 0,
        "used_comments": 0,
        "words": [],
    }


def test_pipeline_reuses_word_cloud_cache_before_downloading(monkeypatch, tmp_path):
    cached = {
        "video_id": "BV1TEST",
        "status": "AVAILABLE",
        "collected_at": datetime.now(UTC).isoformat(),
        "total_comments": 10,
        "used_comments": 8,
        "words": [{"text": "知识", "count": 4}],
    }
    danmaku_parser.save_word_cloud_cache(cached, tmp_path / "BV1TEST.json")
    monkeypatch.setattr(
        video_transcript_pipeline,
        "download_danmaku",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no download")),
    )

    result = video_transcript_pipeline.collect_danmaku_word_cloud(
        "https://www.bilibili.com/video/BV1TEST",
        "BV1TEST",
        {"id": "BV1TEST"},
        cache_dir=tmp_path,
    )

    assert result == cached


def test_pipeline_builds_and_persists_fresh_word_cloud(monkeypatch, tmp_path):
    xml_path = tmp_path / "BV1TEST.danmaku.xml"
    xml_path.write_text(
        '<i><d p="1,1,25,1,0,0,h,1">知识 分析</d></i>',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        video_transcript_pipeline,
        "download_danmaku",
        lambda *args, **kwargs: xml_path,
    )
    monkeypatch.setattr(
        video_transcript_pipeline,
        "build_word_cloud",
        lambda video_id, comments: {
            "video_id": video_id,
            "status": "AVAILABLE",
            "collected_at": "2026-08-20T00:00:00+00:00",
            "total_comments": len(comments),
            "used_comments": 1,
            "words": [{"text": "知识", "count": 1}],
        },
    )

    result = video_transcript_pipeline.collect_danmaku_word_cloud(
        "https://www.bilibili.com/video/BV1TEST",
        "BV1TEST",
        {
            "id": "BV1TEST",
            "subtitles": {"danmaku": [{"ext": "xml"}]},
        },
        cache_dir=tmp_path / "cache",
    )

    assert result["status"] == "AVAILABLE"
    assert danmaku_parser.load_word_cloud_cache(
        tmp_path / "cache" / "BV1TEST.json"
    ) == result


def test_summary_result_exposes_word_cloud_section_and_safe_states():
    sections = summary_result_sections(
        {
            "danmaku_word_cloud": {
                "status": "AVAILABLE",
                "total_comments": 10,
                "used_comments": 8,
                "words": [
                    {"text": "知识", "count": 4},
                    {"text": "分析", "count": 2},
                ],
            }
        }
    )

    assert sections == [
        {
            "key": "danmaku_word_cloud",
            "title": "弹幕词云",
            "kind": "word_cloud",
            "content": {
                "status": "AVAILABLE",
                "total_comments": 10,
                "used_comments": 8,
                "words": [
                    {"text": "知识", "count": 4},
                    {"text": "分析", "count": 2},
                ],
            },
        }
    ]


def test_word_cloud_renderer_shows_scaled_words_counts_and_empty_state():
    available = str(
        render_danmaku_word_cloud(
            {
                "status": "AVAILABLE",
                "total_comments": 10,
                "used_comments": 8,
                "words": [
                    {"text": "知识", "count": 4},
                    {"text": "分析", "count": 2},
                ],
            }
        )
    )
    unavailable = str(
        render_danmaku_word_cloud(
            {"status": "UNAVAILABLE", "reason": "DOWNLOAD_FAILED", "words": []}
        )
    )

    assert 'class="danmaku-word-cloud"' in available
    assert "知识" in available
    assert "4 次" in available
    assert "参与统计 8 条" in available
    assert "弹幕词云暂不可用" in unavailable


def test_word_cloud_renderer_uses_fixed_svg_positions_and_rotated_words():
    rendered = str(
        render_danmaku_word_cloud(
            {
                "status": "AVAILABLE",
                "total_comments": 80,
                "used_comments": 72,
                "words": [
                    {"text": "最大词", "count": 80},
                    {"text": "第二词", "count": 60},
                    {"text": "第三词", "count": 40},
                    {"text": "第四词", "count": 30},
                    {"text": "第五词", "count": 20},
                    {"text": "第六词", "count": 10},
                ],
            }
        )
    )

    assert '<svg class="danmaku-word-cloud-canvas"' in rendered
    assert 'viewBox="0 0 960 260"' in rendered
    assert '<text' in rendered
    assert 'transform="rotate(90 ' in rendered
    assert "danmaku-word-size-" not in rendered
