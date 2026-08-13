"""Behavior tests for the Shiny ranking dashboard boundary."""

import sqlite3
from pathlib import Path

import pytest

from ranking_collector.config import PARTITIONS
from ranking_collector.repository import RepositoryError
from web_app.ranking import server as ranking_server_module
from web_app.ranking import ui as ranking_ui_module
from web_app.ranking.ui import build_ranking_ui, ranking_view_model

_LAYOUT_CSS = Path(__file__).parents[1] / "web_app" / "www" / "layout.css"


def _page_data(collection_status="CURRENT", comparison_status="VALID"):
    comparison = {
        "status": comparison_status,
        "source": collection_status,
        "previous_collected_at": "2026-08-10T10:00:00+00:00",
        "current_collected_at": "2026-08-11T10:00:00+00:00",
        "elapsed_hours": 24.0,
        "turnover_rate": 12.5,
        "retained": [],
        "entered": [],
        "exited": [],
        "metric_changes": [],
        "ranking_risers": [],
        "views_growth_ranking": [],
    }
    return {
        "partition": "全站",
        "collected_at": "2026-08-11T10:00:00+00:00",
        "collection_status": collection_status,
        "items": [{"rank": 1, "title": "Top video", "views": 100}],
        "comparison": comparison,
    }


def test_stale_view_hides_turnover_rate():
    """A stale comparison must not present an invalid turnover percentage."""
    page_data = _page_data(comparison_status="STALE")

    view = ranking_view_model(page_data)

    assert view["status_label"] == "数据已陈旧"
    assert view["turnover_display"] == "超过 24 小时，不可用"
    assert view["empty"] is False


def test_empty_view_has_collection_instruction():
    """A missing current snapshot must direct users to the one-shot collector."""
    empty_page_data = {
        "partition": "全站",
        "collected_at": None,
        "collection_status": "NO_DATA",
        "items": [],
        "comparison": None,
    }

    view = ranking_view_model(empty_page_data)

    assert view["empty"] is True
    assert "--once" in view["empty_message"]


def test_current_view_uses_collection_state_not_validity_state():
    """A successful current collection must not render comparison VALID as a state."""
    view = ranking_view_model(_page_data())

    assert view["status_key"] == "CURRENT"
    assert view["status_label"] == "当前数据"


def test_view_model_keeps_each_collection_and_comparison_state_distinct():
    """Collapsing any supported data state would hide materially different context."""
    current = ranking_view_model(_page_data())
    last_valid = ranking_view_model(_page_data(collection_status="LAST_VALID"))
    no_baseline = ranking_view_model(_page_data(comparison_status="NO_BASELINE"))
    stale = ranking_view_model(_page_data(comparison_status="STALE"))
    empty_current_page = _page_data(comparison_status="EMPTY_CURRENT")
    empty_current_page["items"] = []
    empty_current = ranking_view_model(empty_current_page)

    assert current["status_key"] == "CURRENT"
    assert current["status_label"] == "当前数据"
    assert last_valid["status_key"] == "LAST_VALID"
    assert last_valid["status_label"] == "上一份有效数据"
    assert no_baseline["status_key"] == "NO_BASELINE"
    assert no_baseline["status_label"] == "暂无对比基线"
    assert stale["status_key"] == "STALE"
    assert stale["status_label"] == "数据已陈旧"
    assert empty_current["status_key"] == "EMPTY_CURRENT"
    assert empty_current["status_label"] == "当前榜单为空"
    assert empty_current["empty"] is True


def test_ranking_ui_exposes_enabled_partitions_and_top_100_output():
    """The dashboard needs a selectable configured partition and a tabular ranking."""
    markup = str(build_ranking_ui())

    enabled_names = [
        definition["name"]
        for definition in PARTITIONS.values()
        if definition["enabled"]
    ]
    assert len(enabled_names) == 5
    for partition in enabled_names:
        assert partition in markup
    assert "snapshot" in markup
    assert "freshness" in markup
    assert "metrics" in markup
    assert "changes" in markup
    assert "top_100" in markup


def test_ranking_ui_keeps_primary_summary_and_table_panels_distinct():
    """Flattening the dashboard would break its scan-first visual hierarchy."""
    markup = str(build_ranking_ui())

    assert 'class="ranking-header dashboard-panel"' in markup
    assert 'class="ranking-metrics dashboard-panel"' in markup
    assert 'class="metric-card"' in markup
    assert 'class="ranking-changes dashboard-panel"' in markup
    assert 'class="ranking-table dashboard-panel"' in markup
    assert 'class="ranking-table-scroll"' in markup


def test_empty_ranking_output_cannot_participate_in_the_dashboard_grid():
    """The normal-data empty output must not displace either content panel."""
    markup = str(build_ranking_ui())
    css = _LAYOUT_CSS.read_text(encoding="utf-8")

    assert '<div class="ranking-empty-slot">' in markup
    assert '<div class="shiny-html-output" id="ranking_empty"></div>' in markup
    assert ".ranking-empty-slot {\n  display: contents;\n}" in css
    assert (
        ".ranking-empty-slot > .shiny-html-output:empty {\n  display: none;\n}"
    ) in css


def test_metric_renderer_keeps_each_term_and_value_in_one_semantic_item():
    """Responsive wrapping must never separate a metric label from its value."""
    summary = {
        "item_count": 100,
        "retained_count": 81,
        "entered_count": 19,
        "exited_count": 17,
        "turnover_display": "18.0%",
    }

    markup = " ".join(str(ranking_ui_module.render_ranking_metrics(summary)).split())

    assert markup.startswith('<dl class="ranking-metric-grid">')
    assert markup.count('class="ranking-metric-item"') == 5
    assert (
        '<div class="ranking-metric-item"> <dt>榜单条目</dt> '
        "<dd>100</dd> </div>" in markup
    )
    assert (
        '<div class="ranking-metric-item"> <dt>换血率</dt> '
        "<dd>18.0%</dd> </div>" in markup
    )


def test_metric_css_wraps_complete_items_at_each_responsive_width():
    """Desktop, tablet, and mobile columns must be defined on item wrappers."""
    css = _LAYOUT_CSS.read_text(encoding="utf-8")

    assert ".ranking-metric-grid {" in css
    assert ".ranking-metric-item {" in css
    assert (
        ".ranking-metric-grid {\n"
        "    grid-template-columns: repeat(3, minmax(0, 1fr));\n"
        "  }"
    ) in css
    assert (
        ".ranking-metric-grid {\n"
        "    grid-template-columns: repeat(2, minmax(0, 1fr));\n"
        "  }"
    ) in css


def test_change_visualization_renders_available_video_details_as_lists():
    """Primary change signals should expose the videos and movement behind counts."""
    page_data = _page_data()
    page_data["items"][0].update(
        {"bvid": "BV1TOP", "title": "Top video", "uploader": "示例 UP"}
    )
    page_data["comparison"]["ranking_risers"] = [
        {"bvid": "BV1TOP", "rank_change": 7, "views_per_hour": 1250.0}
    ]
    page_data["comparison"]["views_growth_ranking"] = [
        {"bvid": "BV1TOP", "rank_change": 7, "views_per_hour": 1250.0}
    ]

    view = ranking_view_model(page_data)
    markup = " ".join(
        str(ranking_ui_module.render_ranking_changes(view["changes"])).split()
    )

    assert markup.startswith('<div class="ranking-change-visualization">')
    assert markup.count('<ol class="ranking-change-list">') == 2
    assert markup.count("Top video") == 2
    assert "上升 7 名" in markup
    assert "+1,250 播放/小时" in markup
    assert "示例 UP" in markup


def test_ranking_css_assigns_changes_to_the_dominant_grid_area():
    """The visualization belongs in the wide area and metrics in the side rail."""
    markup = str(build_ranking_ui())
    css = _LAYOUT_CSS.read_text(encoding="utf-8")

    assert 'class="ranking-main-grid"' in markup
    assert 'grid-template-areas: "changes metrics";' in css
    assert "grid-template-columns: minmax(0, 1.55fr) minmax(260px, 0.65fr);" in css
    assert ".ranking-changes {\n  grid-area: changes;" in css
    assert ".ranking-metrics {\n  grid-area: metrics;" in css


def test_top_100_renderer_exposes_every_required_metric_and_escapes_values():
    item = {
        "rank": 1,
        "title": "<script>alert('unsafe')</script>",
        "uploader": "Example UP",
        "views": 1000,
        "likes": 200,
        "coins": 30,
        "favorites": 40,
        "comments": 50,
        "danmaku": 60,
        "shares": 70,
    }

    markup = str(ranking_ui_module.render_ranking_table([item]))
    empty_markup = str(ranking_ui_module.render_ranking_table([]))

    for label in (
        "排名",
        "标题",
        "UP 主",
        "播放",
        "点赞",
        "投币",
        "收藏",
        "评论",
        "弹幕",
        "分享",
    ):
        assert f"<th>{label}</th>" in markup
    for value in ("1000", "200", "30", "40", "50", "60", "70"):
        assert f"<td>{value}</td>" in markup
    assert "&lt;script&gt;alert('unsafe')&lt;/script&gt;" in markup
    assert "<script>" not in markup
    assert empty_markup.count("<th>") == 10
    assert "<td>" not in empty_markup


@pytest.mark.parametrize(
    "error",
    [
        sqlite3.OperationalError("locked at C:/private/ranking.db"),
        OSError("unreadable cookie=secret"),
        RepositoryError("corrupt api_key=secret"),
    ],
)
def test_ranking_loader_maps_expected_read_failures_to_safe_page_state(
    monkeypatch,
    error,
):
    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(ranking_server_module, "build_ranking_page_data", fail)

    page = ranking_server_module.load_ranking_page_data("全站", "ranking.db")

    assert page == {
        "partition": "全站",
        "collected_at": None,
        "collection_status": "QUERY_FAILED",
        "items": [],
        "comparison": None,
    }
    assert "private" not in str(page)
    assert "secret" not in str(page)


def test_ranking_loader_does_not_suppress_programming_errors(monkeypatch):
    def fail(*_args, **_kwargs):
        raise TypeError("broken ranking adapter")

    monkeypatch.setattr(ranking_server_module, "build_ranking_page_data", fail)

    with pytest.raises(TypeError, match="broken ranking adapter"):
        ranking_server_module.load_ranking_page_data("全站", "ranking.db")


def test_empty_ranking_copy_distinguishes_all_material_states():
    no_data = ranking_view_model(
        {
            "partition": "全站",
            "collected_at": None,
            "collection_status": "NO_DATA",
            "items": [],
            "comparison": None,
        }
    )
    failed = ranking_view_model(
        {
            "partition": "全站",
            "collected_at": None,
            "collection_status": "FAILED",
            "items": [],
            "comparison": None,
            "collection_error": "api_key=secret",
        }
    )
    empty_current_data = _page_data(comparison_status="EMPTY_CURRENT")
    empty_current_data["items"] = []
    empty_current = ranking_view_model(empty_current_data)
    query_failed = ranking_view_model(
        {
            "partition": "全站",
            "collected_at": None,
            "collection_status": "QUERY_FAILED",
            "items": [],
            "comparison": None,
        }
    )

    assert "--once" in no_data["empty_message"]
    assert "最近一次采集失败" in failed["empty_message"]
    assert "secret" not in failed["empty_message"]
    assert "当前快照没有榜单条目" in empty_current["empty_message"]
    assert "暂时无法读取排行榜数据" in query_failed["empty_message"]
    assert (
        len(
            {
                no_data["empty_message"],
                failed["empty_message"],
                empty_current["empty_message"],
                query_failed["empty_message"],
            }
        )
        == 4
    )
