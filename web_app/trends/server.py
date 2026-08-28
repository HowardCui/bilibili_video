"""Shiny bindings for the read-only long-term trend dashboard."""

import sqlite3

from shiny import reactive, render, ui

from ranking_collector.repository import RepositoryError

from .queries import load_partition_history
from .service import build_trend_page_data
from .ui import render_line_chart, render_trend_lists, render_trend_summary

METRIC_LABELS = {
    "views": "播放",
    "likes": "点赞",
    "coins": "投币",
    "favorites": "收藏",
    "comments": "评论",
    "danmaku": "弹幕",
    "shares": "分享",
}


def _query_failed_page(partition, range_key, metric):
    return {
        "status": "QUERY_FAILED",
        "partition": partition,
        "range_key": range_key,
        "metric": metric,
        "video_choices": [],
        "video_summary": None,
        "rank_series": [],
        "metric_series": [],
        "turnover_series": [],
        "heat_series": [],
        "lists": {
            "long_running": [],
            "first_entries": [],
            "reentries": [],
        },
        "missing_intervals": [],
        "metadata": {},
    }


def load_trend_page_data(
    partition,
    range_key,
    selected_bvid,
    metric,
    database_path,
    now=None,
):
    """Load and calculate one safe trend page, mapping expected read failures."""
    try:
        history = load_partition_history(
            partition,
            range_key,
            database_path,
            now=now,
        )
        return build_trend_page_data(history, selected_bvid, metric)
    except (RepositoryError, sqlite3.Error, OSError):
        return _query_failed_page(partition, range_key, metric)


def _trend_status(page):
    status = page["status"]
    if status == "QUERY_FAILED":
        return "长期趋势读取失败，请稍后重试。"
    if status == "NO_DATA":
        return "当前范围没有排行榜快照。"
    metadata = page["metadata"]
    message = f"有效快照 {metadata['snapshot_count']} 份"
    if page["missing_intervals"]:
        message += f" · 缺失区间 {len(page['missing_intervals'])} 个"
    if metadata.get("truncated"):
        message += " · 查询结果已达到上限"
    if status == "INSUFFICIENT_DATA":
        message += " · 趋势数据不足"
    return message


def register_trends_server(input, output, session, database_path) -> None:
    """Register reactive trend outputs under the shared ranking partition."""

    @reactive.calc
    def trend_page():
        selected = input.trend_video() or None
        return load_trend_page_data(
            input.ranking_partition(),
            input.trend_range(),
            selected,
            input.trend_metric(),
            database_path,
        )

    @reactive.effect
    def sync_trend_video_choices():
        page = trend_page()
        choices = {
            item["bvid"]: f"{item['title']}（{item['bvid']}）"
            for item in page["video_choices"]
        }
        if not choices:
            choices = {"": "暂无视频"}
        ui.update_select(
            "trend_video",
            choices=choices,
            selected=page.get("selected_bvid") or "",
            session=session,
        )

    @render.ui
    def trend_status():
        return ui.p(_trend_status(trend_page()), class_="trend-status")

    @render.ui
    def trend_summary():
        return render_trend_summary(trend_page())

    @render.ui
    def trend_rank_chart():
        return render_line_chart(
            trend_page()["rank_series"],
            "视频排名轨迹",
            lambda value: f"第 {value:g} 名",
            reverse_y=True,
        )

    @render.ui
    def trend_metric_chart():
        page = trend_page()
        label = METRIC_LABELS.get(page["metric"], "指标")
        return render_line_chart(
            page["metric_series"],
            f"视频{label}轨迹",
            lambda value: f"{value:,.0f}",
        )

    @render.ui
    def trend_turnover_chart():
        return render_line_chart(
            trend_page()["turnover_series"],
            "分区换血率",
            lambda value: f"{value:.1%}",
        )

    @render.ui
    def trend_lists():
        return render_trend_lists(trend_page())


__all__ = ["load_trend_page_data", "register_trends_server"]
