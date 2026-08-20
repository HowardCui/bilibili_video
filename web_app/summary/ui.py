"""Declarative UI and presentation mapping for video-summary tasks."""

import math
from datetime import UTC, datetime

from htmltools import Tag
from shiny import ui

_STAGE_LABELS = {
    "METADATA": "正在获取视频信息",
    "SUBTITLE": "正在获取中文字幕",
    "TRANSCRIPT": "正在整理视频文字稿",
    "SPLIT": "正在划分总结片段",
    "SUMMARIZE_CHUNKS": "正在总结视频片段",
    "MERGE": "正在合并视频总结",
}

_TASK_STATE_CLASSES = {
    "PENDING": "is-pending",
    "PROCESSING": "is-processing",
    "SUCCEEDED": "is-succeeded",
    "FAILED": "is-failed",
}

_DANMAKU_REASONS = {
    "NO_DANMAKU": "该视频当前没有可用的公开弹幕。",
    "DOWNLOAD_FAILED": "弹幕词云暂不可用，视频总结不受影响。",
    "PARSE_FAILED": "弹幕内容暂时无法解析，视频总结不受影响。",
}


def build_summary_ui() -> Tag:
    """Build controls and output regions for the summary workflow."""
    return ui.div(
        ui.tags.section(
            ui.div(
                ui.p("SUMMARY STUDIO", class_="section-kicker"),
                ui.h2("视频总结"),
                ui.p(
                    "粘贴 Bilibili 链接，跟踪处理进度并阅读结构化结果。",
                    class_="section-description",
                ),
                class_="summary-form-copy",
            ),
            ui.div(
                ui.input_text(
                    "summary_video_url",
                    "Bilibili 视频链接",
                    placeholder="https://www.bilibili.com/video/BV...",
                ),
                ui.input_action_button("summary_submit", "开始总结"),
                class_="summary-submit-controls",
            ),
            class_="summary-form dashboard-panel",
        ),
        ui.output_ui("summary_action_error"),
        ui.div(
            ui.tags.section(
                ui.div(
                    ui.h3("当前任务"),
                    ui.p("实时显示队列、处理与完成状态"),
                    class_="panel-heading",
                ),
                ui.output_ui("summary_task_state"),
                ui.output_ui("summary_retry_action"),
                id="summary_task_card",
                class_="summary-current dashboard-panel",
            ),
            ui.tags.section(
                ui.div(
                    ui.h3("总结结果"),
                    ui.p("任务完成后在此呈现内容结构"),
                    class_="panel-heading",
                ),
                ui.output_ui("summary_result_sections"),
                class_="summary-results dashboard-panel",
            ),
            class_="summary-workspace",
        ),
        ui.tags.section(
            ui.div(
                ui.h3("历史任务"),
                ui.p("最近 20 次处理记录"),
                class_="panel-heading",
            ),
            ui.output_ui("summary_history"),
            class_="summary-history dashboard-panel",
        ),
        class_="summary-dashboard",
    )


def summary_task_state_class(status: str | None) -> str:
    """Map one persisted task status to its semantic presentation class."""
    return _TASK_STATE_CLASSES.get(status, "is-unknown")


def summary_result_sections(result) -> list[dict]:
    """Convert an optional public result into ordered display sections."""
    if not isinstance(result, dict):
        return []
    raw_summary = result.get("summary")
    summary = raw_summary if isinstance(raw_summary, dict) else {}

    sections = []
    video_info = {
        key: result[key]
        for key in ("video_id", "video_url", "title", "uploader", "duration")
        if result.get(key) not in (None, "")
    }
    nested_video_info = result.get("video_info")
    if isinstance(nested_video_info, dict):
        video_info.update(
            {
                key: nested_video_info[key]
                for key in ("video_id", "video_url", "title", "uploader", "duration")
                if nested_video_info.get(key) not in (None, "")
            }
        )
    if video_info:
        sections.append(
            {
                "key": "video_info",
                "title": "视频信息",
                "kind": "metadata",
                "content": video_info,
            }
        )

    definitions = (
        (
            "one_line_summary",
            "一句话总结",
            "text",
            summary.get("one_line_summary") or result.get("one_line_summary"),
        ),
        (
            "detailed_summary",
            "详细总结",
            "text",
            summary.get("detailed_summary")
            or summary.get("summary")
            or result.get("detailed_summary")
            or (raw_summary if isinstance(raw_summary, str) else None),
        ),
        (
            "chapters",
            "章节",
            "items",
            summary.get("chapters") or result.get("chapters"),
        ),
        (
            "key_points",
            "关键要点",
            "items",
            summary.get("key_points") or result.get("key_points"),
        ),
        (
            "keywords",
            "关键词",
            "items",
            summary.get("keywords") or result.get("keywords"),
        ),
        (
            "timestamps",
            "时间点",
            "items",
            summary.get("timestamps") or result.get("timestamps"),
        ),
    )
    for key, title, kind, content in definitions:
        if content in (None, "", []):
            continue
        if kind == "text" and not isinstance(content, str):
            continue
        if kind == "items" and not isinstance(content, list):
            continue
        sections.append(
            {
                "key": key,
                "title": title,
                "kind": kind,
                "content": content,
            }
        )
    word_cloud = _public_word_cloud(result.get("danmaku_word_cloud"))
    if word_cloud is not None:
        sections.append(
            {
                "key": "danmaku_word_cloud",
                "title": "弹幕词云",
                "kind": "word_cloud",
                "content": word_cloud,
            }
        )
    return sections


def _public_word_cloud(value):
    if not isinstance(value, dict):
        return None
    status = value.get("status")
    if status not in {"AVAILABLE", "EMPTY", "UNAVAILABLE"}:
        return None
    words = []
    if isinstance(value.get("words"), list):
        for item in value["words"]:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            count = item.get("count")
            if (
                isinstance(text, str)
                and text.strip()
                and isinstance(count, int)
                and not isinstance(count, bool)
                and count > 0
            ):
                words.append({"text": text.strip(), "count": count})
    return {
        key: value[key]
        for key in ("status", "reason", "total_comments", "used_comments")
        if key in value
    } | {"words": words}


def _estimated_word_box(text, font_size, vertical):
    units = sum(1 if ord(character) > 127 else 0.58 for character in text)
    width = max(font_size, units * font_size)
    height = font_size * 1.05
    if vertical:
        return height, width
    return width, height


def _boxes_overlap(candidate, occupied):
    left, top, right, bottom = candidate
    return any(
        left < other_right
        and right > other_left
        and top < other_bottom
        and bottom > other_top
        for other_left, other_top, other_right, other_bottom in occupied
    )


def _word_cloud_layout(words, width=960, height=260):
    maximum = max(item["count"] for item in words)
    minimum = min(item["count"] for item in words)
    spread = max(maximum - minimum, 1)
    occupied = []
    placed = []

    for index, item in enumerate(words[:100]):
        scale = (item["count"] - minimum) / spread
        initial_size = 11 + 58 * (scale ** 0.55)
        vertical = index >= 5 and index % 7 == 5
        position = None
        font_size = initial_size

        for _shrink in range(4):
            word_width, word_height = _estimated_word_box(
                item["text"], font_size, vertical
            )
            for step in range(900):
                angle = step * 0.34
                radius = 0.72 * step
                x = width / 2 + radius * math.cos(angle)
                y = height / 2 + radius * math.sin(angle)
                candidate = (
                    x - word_width / 2 - 2,
                    y - word_height / 2 - 2,
                    x + word_width / 2 + 2,
                    y + word_height / 2 + 2,
                )
                if (
                    candidate[0] >= 4
                    and candidate[1] >= 4
                    and candidate[2] <= width - 4
                    and candidate[3] <= height - 4
                    and not _boxes_overlap(candidate, occupied)
                ):
                    position = (x, y, candidate)
                    break
            if position is not None:
                break
            font_size *= 0.82

        if position is None or font_size < 9:
            continue
        x, y, box = position
        occupied.append(box)
        placed.append(
            {
                **item,
                "x": round(x, 1),
                "y": round(y, 1),
                "font_size": round(font_size, 1),
                "vertical": vertical,
                "color": index % 4,
            }
        )
    return placed


def render_danmaku_word_cloud(word_cloud) -> Tag:
    """Render one responsive, accessible word cloud from safe frequencies."""
    public = _public_word_cloud(word_cloud)
    if public is None:
        return ui.p("弹幕词云数据不可用。", class_="danmaku-word-cloud-state")
    status = public["status"]
    words = public["words"]
    if status == "UNAVAILABLE":
        message = _DANMAKU_REASONS.get(
            public.get("reason"),
            "弹幕词云暂不可用，视频总结不受影响。",
        )
        return ui.p(message, class_="danmaku-word-cloud-state")
    if status == "EMPTY" or not words:
        return ui.p(
            "弹幕清洗后没有可用于词云的关键词。",
            class_="danmaku-word-cloud-state",
        )

    tags = []
    for item in _word_cloud_layout(words):
        transform = None
        if item["vertical"]:
            transform = f"rotate(90 {item['x']} {item['y']})"
        tags.append(
            Tag(
                "text",
                ui.tags.title(f"{item['text']}：{item['count']} 次"),
                item["text"],
                x=item["x"],
                y=item["y"],
                transform=transform,
                style=f"font-size: {item['font_size']}px",
                class_=f"danmaku-word danmaku-word-color-{item['color']}",
            )
        )
    total = public.get("total_comments", 0)
    used = public.get("used_comments", 0)
    return ui.div(
        ui.p(
            f"原始弹幕 {total} 条 · 参与统计 {used} 条",
            class_="danmaku-word-cloud-meta",
        ),
        ui.div(
            ui.tags.svg(
                *tags,
                class_="danmaku-word-cloud-canvas",
                viewBox="0 0 960 260",
                role="img",
                aria_label="弹幕高频词",
            ),
            class_="danmaku-word-cloud",
        ),
    )


def _history_timestamp(value) -> str:
    if not isinstance(value, datetime):
        return "未知时间"
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def summary_history_choices(views: list[dict]) -> dict[str, str]:
    """Build stable task-ID choices with truthful persisted-task metadata."""
    choices = {}
    for view in views:
        task_id = view.get("task_id")
        if not task_id:
            continue
        title = view.get("video_title") or view.get("video_id") or "未知视频"
        created = _history_timestamp(view.get("created_at"))
        finished = (
            _history_timestamp(view.get("finished_at"))
            if view.get("finished_at") is not None
            else "未完成"
        )
        parts = [
            str(title),
            view.get("status_label") or "状态未知",
            f"第 {view.get('attempt_number') or '?'} 次",
            f"提交 {created}",
            f"完成 {finished}",
        ]
        if view.get("status") == "SUCCEEDED":
            parts.append("结果可复用")
        if view.get("retry_of"):
            parts.append("重试任务")
        choices[str(task_id)] = " · ".join(parts)
    return choices


def render_summary_history(
    views: list[dict],
    selected_task_id: str | None = None,
) -> Tag:
    """Render one keyboard-selectable persisted-task history control."""
    choices = summary_history_choices(views)
    if not choices:
        return ui.p("暂无历史任务。")
    selected = selected_task_id if selected_task_id in choices else ""
    return ui.input_radio_buttons(
        "summary_history_selection",
        "选择历史任务",
        choices=choices,
        selected=selected,
    )


def summary_task_view_model(task) -> dict:
    """Convert one persisted task into display-safe presentation values."""
    status = getattr(task, "status", None)
    stage = getattr(task, "stage", None)
    known_statuses = {"PENDING", "PROCESSING", "SUCCEEDED", "FAILED"}
    result = getattr(task, "result", None)
    nested_video_info = result.get("video_info") if isinstance(result, dict) else None
    video_title = result.get("title") if isinstance(result, dict) else None
    if not video_title and isinstance(nested_video_info, dict):
        video_title = nested_video_info.get("title")
    view = {
        "task_id": getattr(task, "task_id", None),
        "video_id": getattr(task, "video_id", None),
        "video_url": getattr(task, "video_url", None),
        "video_title": video_title,
        "attempt_number": getattr(task, "attempt_number", None),
        "retry_of": getattr(task, "retry_of", None),
        "created_at": getattr(task, "created_at", None),
        "updated_at": getattr(task, "updated_at", None),
        "finished_at": getattr(task, "finished_at", None),
        "status": status if status in known_statuses else "UNKNOWN",
        "state_class": summary_task_state_class(status),
        "result_sections": [],
        "error_message": None,
    }
    if status == "PENDING":
        view.update(
            {
                "animation_state": "pending",
                "status_label": "等待处理",
                "stage_label": "任务已进入队列",
                "can_retry": False,
                "should_poll": True,
            }
        )
        return view
    if status == "PROCESSING":
        view.update(
            {
                "animation_state": "processing",
                "status_label": "正在处理",
                "stage_label": _STAGE_LABELS.get(
                    stage,
                    "正在处理（未知阶段）",
                ),
                "can_retry": False,
                "should_poll": True,
            }
        )
        return view
    if status == "SUCCEEDED":
        view.update(
            {
                "animation_state": "success",
                "status_label": "总结完成",
                "stage_label": "视频总结已生成",
                "can_retry": False,
                "should_poll": False,
                "result_sections": summary_result_sections(
                    getattr(task, "result", None)
                ),
            }
        )
        return view
    if status == "FAILED":
        view.update(
            {
                "animation_state": "failed",
                "status_label": "总结失败",
                "stage_label": "任务已停止",
                "can_retry": True,
                "should_poll": False,
                "error_message": "视频总结未能完成，请重试。",
            }
        )
        return view
    view.update(
        {
            "animation_state": "unknown",
            "status_label": "任务状态未知",
            "stage_label": "无法识别已保存的任务状态",
            "can_retry": False,
            "should_poll": False,
        }
    )
    return view


__all__ = [
    "build_summary_ui",
    "render_summary_history",
    "render_danmaku_word_cloud",
    "summary_history_choices",
    "summary_result_sections",
    "summary_task_state_class",
    "summary_task_view_model",
]
