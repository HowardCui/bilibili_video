"""Shiny server bindings and ordinary actions for video-summary tasks."""

from shiny import reactive, render, ui

from .ui import render_summary_history, summary_task_view_model

_CURRENT_TASK_REFRESH_SECONDS = 1.5
_HISTORY_REFRESH_SECONDS = 10.0


def submit_summary_action(service, video_url) -> dict:
    """Submit one URL and return state suitable for the local UI."""
    try:
        task = service.submit(video_url)
    except ValueError:
        return {
            "task": None,
            "error_message": "请输入包含 BV 号的有效 Bilibili 视频链接。",
        }
    except RuntimeError:
        return {
            "task": None,
            "error_message": "总结任务服务暂时不可用，请稍后重试。",
        }
    return {"task": task, "error_message": None}


def retry_summary_action(service, task_id) -> dict:
    """Retry one failed task and return state suitable for the local UI."""
    try:
        task = service.retry(task_id)
    except ValueError:
        return {
            "task": None,
            "error_message": "该任务当前无法重试，请刷新后再试。",
        }
    except RuntimeError:
        return {
            "task": None,
            "error_message": "暂时无法重试，请稍后再试。",
        }
    return {"task": task, "error_message": None}


def select_summary_history_action(service, task_id) -> dict:
    """Resolve one persisted history selection without exposing read details."""
    try:
        task = service.get(task_id)
    except (ValueError, RuntimeError):
        return {
            "task": None,
            "error_message": "暂时无法打开该历史任务，请稍后再试。",
        }
    if task is None:
        return {
            "task": None,
            "error_message": "该历史任务不存在。",
        }
    return {"task": task, "error_message": None}


def poll_summary_task(service, task_id) -> dict:
    """Read one current task and state whether another refresh is needed."""
    if task_id is None:
        return {"task": None, "error_message": None, "should_poll": False}
    try:
        task = service.get(task_id)
    except (ValueError, RuntimeError):
        return {
            "task": None,
            "error_message": "暂时无法读取当前任务，正在自动重试。",
            "should_poll": True,
        }
    if task is None:
        return {
            "task": None,
            "error_message": "当前任务不存在。",
            "should_poll": False,
        }
    return {
        "task": task,
        "error_message": None,
        "should_poll": summary_task_view_model(task)["should_poll"],
    }


def summary_selection_update(current, action) -> dict:
    """Apply an action result while forcing successful same-task refreshes."""
    task = action["task"]
    if task is None:
        return {
            "task_id": current["task_id"],
            "revision": current["revision"],
            "error_message": action["error_message"],
        }
    return {
        "task_id": task.task_id,
        "revision": current["revision"] + 1,
        "error_message": action["error_message"],
    }


def _render_item(item):
    if not isinstance(item, dict):
        return ui.tags.li(str(item))
    text = " · ".join(
        f"{key}: {value}" for key, value in item.items() if value not in (None, "", [])
    )
    return ui.tags.li(text)


def _render_section(section):
    content = section["content"]
    if section["kind"] == "metadata":
        entries = []
        for key, value in content.items():
            entries.extend((ui.tags.dt(str(key)), ui.tags.dd(str(value))))
        body = ui.tags.dl(*entries)
    elif section["kind"] == "items":
        body = ui.tags.ul(*(_render_item(item) for item in content))
    else:
        body = ui.p(str(content))
    return ui.tags.section(ui.h4(section["title"]), body)


def register_summary_server(input, output, session, service) -> None:
    """Register one session's summary actions and cached reactive outputs."""
    _ = output, session
    current_task_id = reactive.value(None)
    current_task_revision = reactive.value(0)
    action_error = reactive.value(None)
    history_revision = reactive.value(0)

    def apply_action(action, *, refresh_history=True):
        selection = summary_selection_update(
            {
                "task_id": current_task_id.get(),
                "revision": current_task_revision.get(),
                "error_message": action_error.get(),
            },
            action,
        )
        action_error.set(selection["error_message"])
        if action["task"] is not None:
            current_task_id.set(selection["task_id"])
            current_task_revision.set(selection["revision"])
            if refresh_history:
                history_revision.set(history_revision.get() + 1)

    @reactive.effect
    @reactive.event(input.summary_submit)
    def handle_summary_submit():
        apply_action(submit_summary_action(service, input.summary_video_url()))

    @reactive.effect
    @reactive.event(input.summary_retry)
    def handle_summary_retry():
        task_id = current_task_id.get()
        if task_id is None:
            action_error.set("当前没有可重试的任务。")
            return
        apply_action(retry_summary_action(service, task_id))

    @reactive.effect
    @reactive.event(input.summary_history_selection, ignore_init=True)
    def handle_summary_history_selection():
        task_id = input.summary_history_selection()
        if task_id:
            apply_action(
                select_summary_history_action(service, task_id),
                refresh_history=False,
            )

    @reactive.calc
    def current_task():
        current_task_revision.get()
        task_id = current_task_id.get()
        outcome = poll_summary_task(service, task_id)
        if outcome["error_message"] is not None:
            action_error.set(outcome["error_message"])
        elif outcome["task"] is not None:
            action_error.set(None)
        if outcome["should_poll"]:
            reactive.invalidate_later(_CURRENT_TASK_REFRESH_SECONDS)
        return outcome["task"]

    @reactive.calc
    def current_view():
        task = current_task()
        return None if task is None else summary_task_view_model(task)

    @reactive.calc
    def history_views():
        history_revision.get()
        reactive.invalidate_later(_HISTORY_REFRESH_SECONDS)
        try:
            tasks = service.list(20)
        except (ValueError, RuntimeError):
            return []
        return [summary_task_view_model(task) for task in tasks]

    @render.ui
    def summary_action_error():
        message = action_error.get()
        if message is None:
            return None
        return ui.div(message, role="alert", class_="summary-action-error")

    @render.ui
    def summary_task_state():
        view = current_view()
        if view is None:
            return ui.p("尚未提交总结任务。")
        return ui.div(
            ui.div(class_=f"task-animation {view['state_class']}"),
            ui.tags.strong(view["status_label"]),
            ui.p(view["stage_label"]),
            ui.p(f"视频：{view['video_id'] or '未知'}"),
            ui.p(f"尝试次数：{view['attempt_number'] or '未知'}"),
            ui.p(view["error_message"], class_="summary-task-error")
            if view["error_message"]
            else None,
            class_="summary-task-state",
        )

    @render.ui
    def summary_retry_action():
        view = current_view()
        if view is None or not view["can_retry"]:
            return None
        return ui.input_action_button("summary_retry", "重试任务")

    @render.ui
    def summary_result_sections():
        view = current_view()
        if view is None:
            return None
        return ui.div(
            *(_render_section(section) for section in view["result_sections"]),
            class_="summary-result-sections",
        )

    @render.ui
    def summary_history():
        views = history_views()
        return render_summary_history(
            views,
            selected_task_id=current_task_id.get(),
        )


__all__ = [
    "poll_summary_task",
    "register_summary_server",
    "retry_summary_action",
    "select_summary_history_action",
    "summary_selection_update",
    "submit_summary_action",
]
