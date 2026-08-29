"""UP 分析页面的 Shiny 响应式绑定。"""

import time

from shiny import reactive, render, ui

from .queries import build_uploader_page_data, list_uploader_choices
from .ui import (
    render_uploader_metrics,
    render_uploader_profile,
    render_uploader_videos,
    uploader_view_model,
)


def register_uploader_server(input, output, session, database_path, service):
    tick = reactive.Value(0)

    @reactive.effect
    def refresh_uploader_choices():
        reactive.invalidate_later(2)
        choices = list_uploader_choices(database_path)
        current = input.uploader_select()
        selected = current if current in choices else next(iter(choices), None)
        ui.update_select("uploader_select", choices=choices, selected=selected)
        tick.set(time.monotonic())

    @reactive.effect
    @reactive.event(input.uploader_collect)
    def start_uploader_collection():
        uploader_id = input.uploader_select()
        if uploader_id:
            service.start(int(uploader_id))
            tick.set(time.monotonic())

    @reactive.calc
    def view():
        tick.get()
        data = build_uploader_page_data(input.uploader_select(), database_path)
        return uploader_view_model(data)

    @render.ui
    def uploader_profile():
        return render_uploader_profile(view())

    @render.ui
    def uploader_metrics():
        return render_uploader_metrics(view())

    @render.ui
    def uploader_videos():
        return render_uploader_videos(view())


__all__ = ["register_uploader_server"]
