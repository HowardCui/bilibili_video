"""UP 分析界面及展示模型。"""

from htmltools import Tag
from shiny import ui

from .visualization import METRIC_LABELS

ERROR_LABELS = {
    "RISK_CONTROL": "平台风控，已停止过度重试，请稍后再试。",
    "NOT_FOUND": "UP 账号不存在或已经不可访问。",
    "RESTRICTED": "UP 投稿访问受限。",
    "REQUEST_FAILED": "网络请求失败，请稍后再试。",
    "API_ERROR": "平台接口暂时不可用。",
    "UNEXPECTED": "采集出现异常，请稍后重试。",
}


def build_uploader_ui() -> Tag:
    return ui.div(
        ui.tags.header(
            ui.div(
                ui.p("UP HISTORY", class_="section-kicker"),
                ui.h2("UP 分析"),
                ui.p(
                    "观察上榜创作者的投稿节奏与长期表现",
                    class_="section-description",
                ),
            ),
            ui.div(
                ui.input_select("uploader_select", "选择已上榜 UP", choices={}),
                ui.input_action_button("uploader_collect", "采集或更新历史投稿"),
                class_="uploader-controls",
            ),
            class_="uploader-header dashboard-panel",
        ),
        ui.div(
            ui.output_ui("uploader_profile"),
            ui.output_ui("uploader_metrics"),
            class_="uploader-overview-grid",
        ),
        ui.tags.section(
            ui.div(
                ui.div(
                    ui.h3("数据可视化"),
                    ui.p("投稿节奏与历史表现", class_="panel-heading"),
                ),
                ui.input_select(
                    "uploader_metric",
                    "表现指标",
                    choices=METRIC_LABELS,
                    selected="views",
                ),
                class_="uploader-visualization-heading",
            ),
            ui.output_ui("uploader_visualization"),
            class_="uploader-visualization dashboard-panel",
        ),
        ui.tags.section(
            ui.div(
                ui.h3("历史投稿"),
                ui.p("最多展示最近 100 条", class_="panel-heading"),
            ),
            ui.div(
                ui.output_ui("uploader_videos"),
                class_="ranking-table-scroll",
                tabindex="0",
            ),
            class_="uploader-videos dashboard-panel",
        ),
        class_="uploader-dashboard",
    )


def uploader_view_model(page_data):
    profile = page_data.get("profile") or {}
    task = page_data.get("task") or {}
    analysis = page_data.get("analysis") or {}
    count = int(analysis.get("video_count") or 0)
    if task.get("status") == "RUNNING":
        collection_message = "历史投稿数据获取中，请勿重复点击。"
    elif task.get("status") == "FAILED":
        collection_message = ERROR_LABELS.get(
            task.get("error_code"), "历史投稿采集失败，请稍后重试。"
        )
    elif task.get("status") == "SUCCEEDED":
        collection_message = "历史投稿采集完成。"
    else:
        collection_message = "尚未采集历史投稿。"
    visualization_notice = None
    if task.get("status") == "FAILED" and page_data.get("videos"):
        visualization_notice = "本轮采集失败，以下图表使用上一份有效数据。"
    return {
        "status": page_data.get("status"),
        "title": profile.get("current_name") or "请选择已确认身份的 UP",
        "uploader_id": profile.get("uploader_id"),
        "collection_message": collection_message,
        "sample_message": f"当前分析基于 {count} 个历史投稿样本。",
        "analysis": analysis,
        "visualization": page_data.get("visualization") or {"status": "NO_DATA"},
        "visualization_notice": visualization_notice,
        "videos": list(page_data.get("videos") or []),
    }


def render_uploader_profile(view):
    identity = f"UID：{view['uploader_id']}" if view["uploader_id"] else "身份未确认"
    return ui.tags.section(
        ui.p("CREATOR", class_="section-kicker"),
        ui.h3(view["title"]),
        ui.p(identity, class_="uploader-identity"),
        ui.p(view["collection_message"], class_="uploader-status"),
        ui.p(view["sample_message"], class_="section-description"),
        class_="uploader-profile dashboard-panel",
    )


def _display_number(value):
    return "样本不足" if value is None else f"{value:,.1f}"


def render_uploader_metrics(view):
    analysis = view["analysis"]
    metrics = (
        ("投稿数", analysis.get("video_count", 0)),
        ("平均播放", _display_number(analysis.get("average_views"))),
        ("播放中位数", _display_number(analysis.get("median_views"))),
        (
            "平均更新间隔/天",
            _display_number(analysis.get("average_publish_interval_days")),
        ),
        ("爆款比例", f"{analysis.get('viral_ratio', 0):.1%}"),
        ("上榜视频均播", _display_number(analysis.get("ranked_average_views"))),
        ("普通视频均播", _display_number(analysis.get("normal_average_views"))),
    )
    return ui.tags.dl(
        *(
            ui.div(ui.tags.dt(label), ui.tags.dd(str(value)), class_="uploader-metric")
            for label, value in metrics
        ),
        class_="uploader-metric-grid dashboard-panel",
    )


def render_uploader_videos(view):
    viral = set(view["analysis"].get("viral_bvids") or [])
    rows = [
        ui.tags.tr(
            ui.tags.td(video["published_at"][:10]),
            ui.tags.td(video["title"]),
            ui.tags.td(video["bvid"]),
            ui.tags.td(f"{int(video.get('views') or 0):,}"),
            ui.tags.td("是" if video["bvid"] in viral else "否"),
        )
        for video in view["videos"][:100]
    ]
    return ui.tags.table(
        ui.tags.thead(
            ui.tags.tr(
                ui.tags.th("发布时间"),
                ui.tags.th("标题"),
                ui.tags.th("BV 号"),
                ui.tags.th("播放"),
                ui.tags.th("爆款"),
            )
        ),
        ui.tags.tbody(*rows),
    )


def _chart_svg(points, ranked_points=False):
    if not points:
        return ui.p("暂无可用数据", class_="uploader-chart-empty")
    width, height = 640, 220
    left, right, top, bottom = 42, 18, 18, 34
    values = [float(point["value"]) for point in points]
    maximum = max(values) or 1
    span = max(1, len(points) - 1)
    coordinates = [
        (
            left + index * (width - left - right) / span,
            top + (maximum - value) * (height - top - bottom) / maximum,
        )
        for index, value in enumerate(values)
    ]
    path = " ".join(
        f"{'M' if index == 0 else 'L'} {x:.1f} {y:.1f}"
        for index, (x, y) in enumerate(coordinates)
    )
    circles = []
    for point, (x, y) in zip(points, coordinates, strict=True):
        point_class = (
            "uploader-chart-point uploader-chart-point-ranked"
            if ranked_points and point.get("ranked")
            else "uploader-chart-point"
        )
        circles.append(
            Tag(
                "circle",
                Tag(
                    "title",
                    f"{point.get('label') or point.get('title')}: "
                    f"{int(point['value']):,}"
                ),
                cx=f"{x:.1f}",
                cy=f"{y:.1f}",
                r="4",
                class_=point_class,
            )
        )
    return Tag(
        "svg",
        Tag(
            "line",
            x1=str(left),
            y1=str(height - bottom),
            x2=str(width - right),
            y2=str(height - bottom),
            class_="uploader-chart-axis",
        ),
        Tag("path", d=path, class_="uploader-chart-line"),
        *circles,
        viewBox=f"0 0 {width} {height}",
        role="img",
        class_="uploader-chart-svg",
    )


def _number(value):
    return "样本不足" if value is None else f"{value:,.1f}"


def render_uploader_visualization(view):
    chart = view.get("visualization") or {}
    if chart.get("status") == "QUERY_FAILED":
        return ui.p(
            "可视化查询失败，UP 档案和历史投稿仍可继续查看。",
            class_="uploader-chart-empty",
        )
    if chart.get("status") != "READY":
        return ui.p("选择 UP 并采集历史投稿后显示图表。", class_="uploader-chart-empty")
    distribution = chart["distribution"]
    comparison = chart["comparison"]
    viral = chart["viral"]
    date_range = (
        f"{chart['range_start'][:10]} → {chart['range_end'][:10]}"
        if chart.get("range_start") and chart.get("range_end")
        else "时间范围未知"
    )
    updated = chart["updated_at"][:10] if chart.get("updated_at") else "未知"
    sample_copy = f"样本：{chart['sample_count']} 条"
    return ui.div(
        (
            ui.p(
                view["visualization_notice"],
                class_="uploader-visualization-notice",
            )
            if view.get("visualization_notice")
            else None
        ),
        ui.div(
            ui.h4("投稿频率"),
            ui.p(sample_copy, class_="uploader-chart-caption"),
            _chart_svg(chart["monthly_frequency"]),
            class_="uploader-chart-card",
        ),
        ui.div(
            ui.h4(f"视频表现趋势 · {chart['metric_label']}"),
            ui.p(sample_copy, class_="uploader-chart-caption"),
            ui.p("● 上榜投稿　● 普通投稿", class_="uploader-chart-legend"),
            (
                _chart_svg(chart["performance_series"], ranked_points=True)
                if chart["performance_status"] == "READY"
                else ui.p("暂无可用指标", class_="uploader-chart-empty")
            ),
            class_="uploader-chart-card",
        ),
        ui.div(
            ui.h4("历史表现分布"),
            ui.p(
                f"样本：{distribution['sample_count']} 条",
                class_="uploader-chart-caption",
            ),
            (
                ui.p("暂无可用指标", class_="uploader-chart-empty")
                if distribution["status"] == "NO_METRIC"
                else ui.div(
                    ui.p(f"平均 {_number(distribution['average'])}"),
                    ui.p(f"中位数 {_number(distribution['median'])}"),
                    ui.p(f"高位区间 {_number(distribution['high'])}"),
                )
            ),
            class_="uploader-chart-card uploader-stat-card",
        ),
        ui.div(
            ui.h4("上榜与普通投稿"),
            ui.p(
                f"上榜投稿 {comparison['ranked']['sample_count']} 条 · "
                f"平均 {_number(comparison['ranked']['average'])}"
            ),
            ui.p(
                f"普通投稿 {comparison['normal']['sample_count']} 条 · "
                f"平均 {_number(comparison['normal']['average'])}"
            ),
            (
                ui.p("样本不足，暂不形成稳定对比。", class_="uploader-chart-empty")
                if comparison["status"] == "INSUFFICIENT"
                else None
            ),
            class_="uploader-chart-card uploader-stat-card",
        ),
        ui.div(
            ui.h4("爆款比例"),
            ui.p(f"{viral['ratio']:.1%}"),
            ui.p(
                f"{viral['count']} / {viral['sample_count']} 条",
                class_="panel-heading",
            ),
            (
                ui.p("样本不足，暂不判断爆款比例。", class_="uploader-chart-empty")
                if viral["status"] == "INSUFFICIENT"
                else None
            ),
            class_="uploader-chart-card uploader-stat-card",
        ),
        ui.p(
            f"统计范围：{date_range}　数据更新：{updated}",
            class_="uploader-visualization-meta",
        ),
        class_="uploader-chart-grid",
    )


__all__ = [
    "build_uploader_ui",
    "render_uploader_metrics",
    "render_uploader_profile",
    "render_uploader_visualization",
    "render_uploader_videos",
    "uploader_view_model",
]
