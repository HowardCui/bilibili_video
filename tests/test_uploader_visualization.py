from datetime import UTC, datetime
from pathlib import Path

from ranking_collector.repository import connect_database, initialize_database
from uploader_analysis.repository import initialize_uploader_database
from web_app.uploader.queries import build_uploader_page_data
from web_app.uploader.ui import (
    build_uploader_ui,
    render_uploader_visualization,
    uploader_view_model,
)
from web_app.uploader.visualization import build_uploader_visualization


def _video(bvid, published_at, views, likes=0):
    return {
        "bvid": bvid,
        "title": f"视频 {bvid}",
        "published_at": published_at,
        "views": views,
        "likes": likes,
        "coins": 0,
        "favorites": 0,
        "comments": 0,
        "danmaku": 0,
        "shares": 0,
        "updated_at": "2026-08-30T12:00:00+00:00",
    }


def test_visualization_aggregates_months_and_marks_ranked_points():
    videos = [
        _video("BV1", "2026-01-03T00:00:00+00:00", 100, 10),
        _video("BV2", "2026-01-20T00:00:00+00:00", 300, 30),
        _video("BV3", "2026-02-02T00:00:00+00:00", 200, 20),
    ]
    analysis = {
        "viral_bvids": ["BV2"],
        "viral_ratio": 1 / 3,
        "viral_threshold": 250,
    }

    result = build_uploader_visualization(
        videos, {"BV2"}, analysis, metric="likes"
    )

    assert result["status"] == "READY"
    assert result["metric_label"] == "点赞"
    assert [point["value"] for point in result["monthly_frequency"]] == [2, 1]
    assert [point["bvid"] for point in result["performance_series"]] == [
        "BV1",
        "BV2",
        "BV3",
    ]
    assert result["performance_series"][1]["ranked"] is True
    assert result["distribution"]["median"] == 20
    assert result["comparison"]["ranked"]["sample_count"] == 1
    assert result["comparison"]["normal"]["sample_count"] == 2
    assert result["viral"]["count"] == 1


def test_visualization_reports_insufficient_and_zero_metric_states():
    one_video = [_video("BV1", "2026-01-03T00:00:00+00:00", 100)]
    result = build_uploader_visualization(
        one_video,
        set(),
        {"viral_bvids": [], "viral_ratio": 0, "viral_threshold": None},
        metric="coins",
    )

    assert result["performance_status"] == "NO_METRIC"
    assert result["distribution"]["status"] == "NO_METRIC"
    assert result["comparison"]["status"] == "INSUFFICIENT"
    assert result["viral"]["status"] == "INSUFFICIENT"


def test_visualization_limits_dense_series_without_mutating_source():
    videos = [
        _video(
            f"BV{index}",
            f"2026-01-{index % 28 + 1:02d}T00:00:00+00:00",
            index,
        )
        for index in range(300)
    ]
    original = list(videos)

    result = build_uploader_visualization(
        videos,
        set(),
        {"viral_bvids": [], "viral_ratio": 0, "viral_threshold": 0},
    )

    assert len(result["performance_series"]) == 240
    assert videos == original


def test_uploader_visualization_is_above_history_and_renders_native_charts():
    page_markup = str(build_uploader_ui())
    assert page_markup.index("uploader_visualization") < page_markup.index(
        "uploader_videos"
    )
    assert "uploader_metric" in page_markup

    visualization = build_uploader_visualization(
        [
            _video("BV1", "2026-01-03T00:00:00+00:00", 100),
            _video("BV2", "2026-02-03T00:00:00+00:00", 300),
        ],
        {"BV2"},
        {"viral_bvids": ["BV2"], "viral_ratio": 0.5, "viral_threshold": 200},
    )
    markup = str(render_uploader_visualization({"visualization": visualization}))

    assert "投稿频率" in markup
    assert "视频表现趋势" in markup
    assert "历史表现分布" in markup
    assert "上榜与普通投稿" in markup
    assert "爆款比例" in markup
    assert "<svg" in markup
    assert "上榜投稿" in markup
    assert "2026-01-03 → 2026-02-03" in markup
    assert "数据更新：2026-08-30" in markup
    assert "样本：2 条" in markup


def test_page_query_builds_selected_metric_without_modifying_database(tmp_path):
    database_path = tmp_path / "ranking.db"
    initialize_database(database_path)
    initialize_uploader_database(database_path)
    now = datetime(2026, 8, 30, tzinfo=UTC).isoformat()
    with connect_database(database_path) as connection:
        connection.execute(
            """INSERT INTO uploader_profiles (
                uploader_id,current_name,first_ranked_at,last_ranked_at,updated_at
            ) VALUES (123,'示例 UP',?,?,?)""",
            (now, now, now),
        )
        for index, likes in enumerate((10, 30), start=1):
            connection.execute(
                """INSERT INTO uploader_videos (
                    uploader_id,bvid,title,published_at,views,likes,updated_at
                ) VALUES (?,?,?,?,?,?,?)""",
                (
                    123,
                    f"BV{index}",
                    f"视频 {index}",
                    f"2026-0{index}-01T00:00:00+00:00",
                    index * 100,
                    likes,
                    now,
                ),
            )
    before = database_path.read_bytes()

    page = build_uploader_page_data(123, database_path, metric="likes")

    assert page["status"] == "READY"
    assert page["visualization"]["metric"] == "likes"
    assert page["visualization"]["distribution"]["median"] == 20
    assert database_path.read_bytes() == before


def test_visualization_query_failure_does_not_hide_uploader_history(tmp_path):
    database_path = tmp_path / "ranking.db"
    initialize_database(database_path)
    initialize_uploader_database(database_path)
    now = datetime(2026, 8, 30, tzinfo=UTC).isoformat()
    with connect_database(database_path) as connection:
        connection.execute(
            """INSERT INTO uploader_profiles (
                uploader_id,current_name,first_ranked_at,last_ranked_at,updated_at
            ) VALUES (123,'示例 UP',?,?,?)""",
            (now, now, now),
        )
        connection.execute(
            """INSERT INTO uploader_videos (
                uploader_id,bvid,title,published_at,views,updated_at
            ) VALUES (123,'BV1','视频 1','2026-01-01T00:00:00+00:00',100,?)""",
            (now,),
        )

    page = build_uploader_page_data(123, database_path, metric="unsupported")

    assert page["status"] == "READY"
    assert len(page["videos"]) == 1
    assert page["visualization"]["status"] == "QUERY_FAILED"


def test_visualization_explains_zero_metric_small_sample_and_stale_data():
    videos = [_video("BV1", "2026-01-03T00:00:00+00:00", 100)]
    visualization = build_uploader_visualization(
        videos,
        set(),
        {"viral_bvids": [], "viral_ratio": 0, "viral_threshold": None},
        metric="coins",
    )
    view = uploader_view_model(
        {
            "status": "READY",
            "profile": {"current_name": "示例 UP", "uploader_id": 123},
            "task": {"status": "FAILED", "error_code": "REQUEST_FAILED"},
            "videos": videos,
            "analysis": {"video_count": 1},
            "visualization": visualization,
        }
    )

    markup = str(render_uploader_visualization(view))

    assert "本轮采集失败，以下图表使用上一份有效数据" in markup
    assert "暂无可用指标" in markup
    assert "样本不足" in markup


def test_visualization_css_uses_existing_tokens_and_mobile_single_column():
    css = (Path(__file__).parents[1] / "web_app" / "www" / "layout.css").read_text(
        encoding="utf-8"
    )

    assert ".uploader-chart-grid" in css
    assert ".uploader-chart-svg" in css
    assert "var(--" in css
    assert "grid-template-columns: 1fr" in css
