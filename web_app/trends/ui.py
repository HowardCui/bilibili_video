"""Declarative controls and native SVG rendering for ranking trends."""

from datetime import datetime

from htmltools import Tag
from shiny import ui

RANGE_CHOICES = {
    "24H": "24 小时",
    "7D": "7 天",
    "30D": "30 天",
    "ALL": "全部",
}
METRIC_CHOICES = {
    "views": "播放",
    "likes": "点赞",
    "coins": "投币",
    "favorites": "收藏",
    "comments": "评论",
    "danmaku": "弹幕",
    "shares": "分享",
}


def _format_time(value):
    if not isinstance(value, datetime):
        return "未知时间"
    return value.strftime("%Y-%m-%d %H:%M")


def build_trends_ui() -> Tag:
    """Build long-term trend controls and output regions."""
    return ui.tags.section(
        ui.div(
            ui.div(
                ui.h3("长期趋势"),
                ui.p("跨多份快照观察视频和分区变化"),
            ),
            ui.div(
                ui.input_select(
                    "trend_range", "时间范围", RANGE_CHOICES, selected="7D"
                ),
                ui.input_select("trend_video", "观察视频", {"": "暂无视频"}),
                ui.input_select(
                    "trend_metric", "累计指标", METRIC_CHOICES, selected="views"
                ),
                class_="trend-controls",
            ),
            class_="trend-heading",
        ),
        ui.output_ui("trend_status"),
        ui.output_ui("trend_summary"),
        ui.div(
            ui.tags.section(
                ui.h4("排名轨迹"),
                ui.output_ui("trend_rank_chart"),
                class_="trend-chart-card",
            ),
            ui.tags.section(
                ui.h4("指标轨迹"),
                ui.output_ui("trend_metric_chart"),
                class_="trend-chart-card",
            ),
            ui.tags.section(
                ui.h4("分区换血率"),
                ui.output_ui("trend_turnover_chart"),
                class_="trend-chart-card trend-chart-wide",
            ),
            class_="trend-chart-grid",
        ),
        ui.output_ui("trend_lists"),
        class_="ranking-trends dashboard-panel",
    )


def _chart_coordinates(series, reverse_y):
    valid = [
        (index, point)
        for index, point in enumerate(series)
        if point["value"] is not None
    ]
    if not valid:
        return {}
    values = [point["value"] for _index, point in valid]
    minimum = min(values)
    maximum = max(values)
    spread = maximum - minimum or 1
    x_spread = max(len(series) - 1, 1)
    coordinates = {}
    for index, point in valid:
        x = 54 + (index / x_spread) * 642
        ratio = (point["value"] - minimum) / spread
        y = 34 + ratio * 180 if reverse_y else 214 - ratio * 180
        coordinates[index] = (round(x, 2), round(y, 2))
    return coordinates


def _line_segments(series, coordinates):
    segments = []
    current = []
    for index, point in enumerate(series):
        if point["value"] is None:
            if len(current) >= 2:
                segments.append(current)
            current = []
            continue
        current.append(coordinates[index])
    if len(current) >= 2:
        segments.append(current)
    return segments


def render_line_chart(
    series,
    title,
    value_label,
    reverse_y=False,
    missing_intervals=None,
) -> Tag:
    """Render one responsive native SVG line chart from safe primitive points."""
    _ = missing_intervals
    if not series or not any(point.get("value") is not None for point in series):
        return ui.p("趋势数据不足。", class_="trend-empty")
    coordinates = _chart_coordinates(series, reverse_y)
    grid = [
        Tag(
            "line",
            x1=54,
            y1=y,
            x2=696,
            y2=y,
            class_="trend-chart-grid-line",
        )
        for y in (34, 79, 124, 169, 214)
    ]
    paths = []
    for segment in _line_segments(series, coordinates):
        command = "M " + " L ".join(f"{x} {y}" for x, y in segment)
        paths.append(Tag("path", d=command, class_="trend-chart-line"))
    points = []
    for index, point in enumerate(series):
        if point.get("value") is None:
            continue
        x, y = coordinates[index]
        points.append(
            Tag(
                "circle",
                Tag(
                    "title",
                    f"{_format_time(point.get('at'))} · {value_label(point['value'])}",
                ),
                cx=x,
                cy=y,
                r=4,
                class_="trend-chart-point",
            )
        )
    return Tag(
        "svg",
        Tag("title", title),
        *grid,
        *paths,
        *points,
        class_="trend-chart-svg",
        viewBox="0 0 720 260",
        role="img",
        aria_label=title,
        data_reverse_y="true" if reverse_y else "false",
    )


def render_trend_summary(page_data) -> Tag:
    """Render the selected video's range-scoped ranking summary."""
    summary = page_data.get("video_summary")
    if not summary:
        return ui.p("当前范围没有可展示的视频趋势。", class_="trend-empty")
    current = (
        f"第 {summary['current_rank']} 名"
        if summary["current_rank"] is not None
        else "当前未在榜"
    )
    values = (
        ("范围内首次上榜", _format_time(summary["first_ranked_at"])),
        ("最后在榜", _format_time(summary["last_ranked_at"])),
        ("在榜次数", f"累计 {summary['cumulative_count']} 次"),
        ("连续在榜", f"{summary['consecutive_count']} 次"),
        ("最高 / 最低", f"第 {summary['best_rank']} / {summary['worst_rank']} 名"),
        ("当前排名", current),
    )
    return ui.div(
        ui.div(
            ui.strong(summary["title"]),
            ui.span(summary["bvid"]),
            class_="trend-video-identity",
        ),
        ui.tags.dl(
            *(ui.div(ui.tags.dt(label), ui.tags.dd(value)) for label, value in values),
            class_="trend-summary-grid",
        ),
    )


def _trend_list(title, entries, value):
    return ui.tags.section(
        ui.h4(title),
        (
            ui.tags.ol(
                *(
                    ui.tags.li(
                        ui.strong(entry["title"]),
                        ui.span(value(entry)),
                    )
                    for entry in entries[:10]
                )
            )
            if entries
            else ui.p("当前范围暂无记录。", class_="trend-empty")
        ),
    )


def render_trend_lists(page_data) -> Tag:
    """Render long-running, first-entry, and re-entry lists."""
    lists = page_data.get("lists") or {}
    return ui.div(
        _trend_list(
            "长期在榜",
            lists.get("long_running") or [],
            lambda entry: f"{entry['count']} 份快照",
        ),
        _trend_list(
            "首次上榜",
            lists.get("first_entries") or [],
            lambda entry: _format_time(entry["at"]),
        ),
        _trend_list(
            "重新上榜",
            lists.get("reentries") or [],
            lambda entry: _format_time(entry["at"]),
        ),
        class_="trend-list-grid",
    )


__all__ = [
    "build_trends_ui",
    "render_line_chart",
    "render_trend_lists",
    "render_trend_summary",
]
