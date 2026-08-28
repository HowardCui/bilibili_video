"""Declarative UI and presentation mapping for the ranking dashboard."""

from htmltools import Tag
from shiny import ui

from ranking_collector.config import PARTITIONS
from web_app.trends.ui import build_trends_ui

STATUS_LABELS = {
    "CURRENT": "当前数据",
    "LAST_VALID": "上一份有效数据",
    "NO_BASELINE": "暂无对比基线",
    "STALE": "数据已陈旧",
    "EMPTY_CURRENT": "当前榜单为空",
    "NO_DATA": "暂无数据",
    "FAILED": "采集失败",
    "QUERY_FAILED": "读取失败",
}

_METRIC_LABELS = (
    ("item_count", "榜单条目"),
    ("retained_count", "留榜"),
    ("entered_count", "新进"),
    ("exited_count", "退出"),
    ("turnover_display", "换血率"),
)


def _change_item(change: dict, items_by_bvid: dict) -> dict:
    item = items_by_bvid.get(change.get("bvid"), {})
    return {
        **change,
        "title": item.get("title") or change.get("bvid") or "未知视频",
        "uploader": item.get("uploader") or "未知 UP 主",
    }


def build_ranking_ui() -> Tag:
    """Build the read-only controls and output regions for one ranking view."""
    choices = {
        definition["name"]: definition["name"]
        for definition in PARTITIONS.values()
        if definition["enabled"]
    }
    return ui.div(
        ui.tags.header(
            ui.div(
                ui.p("RANKING PULSE", class_="section-kicker"),
                ui.h2("排行榜"),
                ui.p(
                    "捕捉站内热度与榜单变化",
                    class_="section-description",
                ),
            ),
            ui.div(
                ui.input_select(
                    "ranking_partition",
                    "观察分区",
                    choices=choices,
                ),
                class_="ranking-partition-control",
            ),
            ui.div(
                ui.div(
                    ui.span("快照", class_="summary-label"),
                    ui.output_text("ranking_snapshot"),
                    class_="snapshot",
                ),
                ui.div(
                    ui.span("状态", class_="summary-label"),
                    ui.output_text("ranking_freshness"),
                    class_="freshness",
                ),
                class_="ranking-snapshot-summary",
            ),
            class_="ranking-header dashboard-panel",
        ),
        ui.div(
            ui.output_ui("ranking_empty"),
            class_="ranking-empty-slot",
        ),
        ui.div(
            ui.tags.section(
                ui.div(
                    ui.h3("变化脉冲"),
                    ui.p("排名与播放增长的活跃信号"),
                    class_="panel-heading",
                ),
                ui.output_ui("ranking_changes"),
                class_="ranking-changes dashboard-panel",
            ),
            ui.tags.section(
                ui.div(
                    ui.h3("指标摘要"),
                    ui.p("当前快照的关键规模与流动性"),
                    class_="panel-heading",
                ),
                ui.div(ui.output_ui("ranking_metrics"), class_="metric-card"),
                class_="ranking-metrics dashboard-panel",
            ),
            class_="ranking-main-grid",
        ),
        ui.tags.section(
            ui.div(
                ui.h3("Top 100"),
                ui.p("按当前分区快照排列"),
                class_="panel-heading",
            ),
            ui.div(
                ui.output_ui("ranking_top_100"),
                class_="ranking-table-scroll",
                tabindex="0",
                role="region",
                aria_label="排行榜表格，可横向滚动",
            ),
            class_="ranking-table dashboard-panel",
        ),
        build_trends_ui(),
        class_="ranking-dashboard",
    )


def render_ranking_metrics(summary: dict) -> Tag:
    """Render each metric term and value as one responsive definition group."""
    return ui.tags.dl(
        *(
            ui.div(
                ui.tags.dt(label),
                ui.tags.dd(str(summary[key])),
                class_="ranking-metric-item",
            )
            for key, label in _METRIC_LABELS
        ),
        class_="ranking-metric-grid",
    )


def _render_change_list(changes: list[dict], value_label) -> Tag:
    if not changes:
        return ui.p("本次快照暂无变化信号。", class_="ranking-change-empty")
    return ui.tags.ol(
        *(
            ui.tags.li(
                ui.div(
                    ui.tags.strong(change["title"]),
                    ui.span(change["uploader"]),
                    class_="ranking-change-identity",
                ),
                ui.span(value_label(change), class_="ranking-change-value"),
            )
            for change in changes[:5]
        ),
        class_="ranking-change-list",
    )


def render_ranking_changes(changes: dict) -> Tag:
    """Render the strongest available rank and growth signals with details."""
    risers = changes["ranking_risers"]
    growth = changes["views_growth_ranking"]
    return ui.div(
        ui.tags.section(
            ui.div(
                ui.h4("排名跃升"),
                ui.span(str(len(risers)), class_="ranking-change-count"),
                class_="ranking-change-heading",
            ),
            _render_change_list(
                risers,
                lambda change: f"上升 {change.get('rank_change', 0)} 名",
            ),
        ),
        ui.tags.section(
            ui.div(
                ui.h4("播放增速"),
                ui.span(str(len(growth)), class_="ranking-change-count"),
                class_="ranking-change-heading",
            ),
            _render_change_list(
                growth,
                lambda change: f"{change.get('views_per_hour', 0):+,.0f} 播放/小时",
            ),
        ),
        class_="ranking-change-visualization",
    )


def render_ranking_table(items: list[dict]) -> Tag:
    """Render the complete Top 100 metric contract as a semantic table."""
    columns = (
        ("rank", "排名"),
        ("title", "标题"),
        ("uploader", "UP 主"),
        ("views", "播放"),
        ("likes", "点赞"),
        ("coins", "投币"),
        ("favorites", "收藏"),
        ("comments", "评论"),
        ("danmaku", "弹幕"),
        ("shares", "分享"),
    )
    rows = [
        ui.tags.tr(*(ui.tags.td(str(item.get(key, ""))) for key, _label in columns))
        for item in items
    ]
    return ui.tags.table(
        ui.tags.thead(ui.tags.tr(*(ui.tags.th(label) for _key, label in columns))),
        ui.tags.tbody(*rows),
    )


def ranking_view_model(page_data) -> dict:
    """Convert read-only page data into display-safe ranking dashboard values."""
    comparison = page_data.get("comparison") or {}
    collection_status = page_data.get("collection_status", "NO_DATA")
    comparison_status = comparison.get("status")
    status_key = collection_status
    if comparison_status in {"NO_BASELINE", "STALE", "EMPTY_CURRENT"}:
        status_key = comparison_status

    items = list(page_data.get("items") or [])
    turnover_rate = comparison.get("turnover_rate")
    if comparison_status == "STALE":
        turnover_display = "超过 24 小时，不可用"
    elif turnover_rate is None:
        turnover_display = "暂无数据"
    else:
        turnover_display = f"{turnover_rate:.1%}"

    empty = not items
    empty_message = ""
    if empty and status_key == "EMPTY_CURRENT":
        empty_message = "当前快照没有榜单条目。"
    elif empty and collection_status == "FAILED":
        empty_message = "最近一次采集失败，且暂无可展示的有效榜单。"
    elif empty and collection_status == "QUERY_FAILED":
        empty_message = "暂时无法读取排行榜数据，请稍后重试。"
    elif empty:
        empty_message = (
            "暂无排行榜数据。请先运行 "
            "python -m ranking_collector.ranking_collector_pipeline --once 采集数据。"
        )

    items_by_bvid = {item.get("bvid"): item for item in items if item.get("bvid")}
    return {
        "partition": page_data.get("partition"),
        "collection_status": collection_status,
        "comparison_status": comparison_status,
        "status_key": status_key,
        "status_label": STATUS_LABELS.get(status_key, "状态未知"),
        "snapshot_time": page_data.get("collected_at") or "暂无快照",
        "freshness": STATUS_LABELS.get(status_key, "状态未知"),
        "empty": empty,
        "empty_message": empty_message,
        "items": items,
        "metric_summary": {
            "item_count": len(items),
            "retained_count": len(comparison.get("retained") or []),
            "entered_count": len(comparison.get("entered") or []),
            "exited_count": len(comparison.get("exited") or []),
            "turnover_display": turnover_display,
        },
        "turnover_display": turnover_display,
        "changes": {
            "ranking_risers": [
                _change_item(change, items_by_bvid)
                for change in comparison.get("ranking_risers") or []
            ],
            "views_growth_ranking": [
                _change_item(change, items_by_bvid)
                for change in comparison.get("views_growth_ranking") or []
            ],
        },
    }


__all__ = [
    "build_ranking_ui",
    "ranking_view_model",
    "render_ranking_changes",
    "render_ranking_metrics",
    "render_ranking_table",
]
