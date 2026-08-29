"""UP 分析界面及展示模型。"""

from htmltools import Tag
from shiny import ui

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
    return {
        "status": page_data.get("status"),
        "title": profile.get("current_name") or "请选择已确认身份的 UP",
        "uploader_id": profile.get("uploader_id"),
        "collection_message": collection_message,
        "sample_message": f"当前分析基于 {count} 个历史投稿样本。",
        "analysis": analysis,
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


__all__ = [
    "build_uploader_ui",
    "render_uploader_metrics",
    "render_uploader_profile",
    "render_uploader_videos",
    "uploader_view_model",
]
