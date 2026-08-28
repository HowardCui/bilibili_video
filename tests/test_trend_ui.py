from datetime import UTC, datetime, timedelta

from web_app.trends.ui import (
    build_trends_ui,
    render_line_chart,
    render_trend_lists,
    render_trend_summary,
)


def test_line_chart_renders_safe_native_svg_points_and_reverse_axis():
    start = datetime(2026, 8, 1, tzinfo=UTC)
    rendered = str(
        render_line_chart(
            [
                {"at": start, "value": 3},
                {"at": start + timedelta(hours=1), "value": 1},
            ],
            "排名 <轨迹>",
            lambda value: f"第 {value} 名",
            reverse_y=True,
        )
    )

    assert '<svg class="trend-chart-svg"' in rendered
    assert 'viewBox="0 0 720 260"' in rendered
    assert 'data-reverse-y="true"' in rendered
    assert "排名 &lt;轨迹&gt;" in rendered
    assert "第 3 名" in rendered
    assert rendered.count("<circle") == 2


def test_line_chart_breaks_paths_at_missing_values_and_handles_single_point():
    start = datetime(2026, 8, 1, tzinfo=UTC)
    broken = str(
        render_line_chart(
            [
                {"at": start, "value": 1},
                {"at": start + timedelta(hours=1), "value": None},
                {"at": start + timedelta(hours=2), "value": 2},
            ],
            "播放轨迹",
            str,
        )
    )
    single = str(
        render_line_chart(
            [{"at": start, "value": 10}],
            "单点",
            str,
        )
    )
    empty = str(render_line_chart([], "空图", str))

    assert broken.count('class="trend-chart-line"') == 0
    assert broken.count("<circle") == 2
    assert single.count("<circle") == 1
    assert "趋势数据不足" in empty


def test_trends_ui_exposes_controls_and_outputs():
    rendered = str(build_trends_ui())

    for identifier in (
        "trend_range",
        "trend_video",
        "trend_metric",
        "trend_status",
        "trend_summary",
        "trend_rank_chart",
        "trend_metric_chart",
        "trend_turnover_chart",
        "trend_lists",
    ):
        assert identifier in rendered
    assert 'value="24H">24 小时</option>' in rendered
    assert 'value="views" selected="">播放</option>' in rendered


def test_trend_summary_and_lists_render_explicit_data():
    at = datetime(2026, 8, 1, tzinfo=UTC)
    page = {
        "status": "AVAILABLE",
        "video_summary": {
            "bvid": "BV1",
            "title": "Video One",
            "first_ranked_at": at,
            "last_ranked_at": at,
            "consecutive_count": 2,
            "cumulative_count": 3,
            "best_rank": 1,
            "worst_rank": 8,
            "current_rank": None,
            "reentry_count": 1,
        },
        "lists": {
            "long_running": [{"bvid": "BV1", "title": "Video One", "count": 3}],
            "first_entries": [{"bvid": "BV1", "title": "Video One", "at": at}],
            "reentries": [{"bvid": "BV1", "title": "Video One", "at": at}],
        },
    }

    summary = str(render_trend_summary(page))
    lists = str(render_trend_lists(page))

    assert "范围内首次上榜" in summary
    assert "当前未在榜" in summary
    assert "累计 3 次" in summary
    assert "重新上榜" in lists
    assert "Video One" in lists
