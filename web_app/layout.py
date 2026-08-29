"""UI construction for the Shiny application."""

from htmltools import Tag
from shiny import ui

from .ranking.ui import build_ranking_ui
from .summary.ui import build_summary_ui
from .uploader.ui import build_uploader_ui


def build_app_ui() -> Tag:
    """Build the application's two-entry navigation shell."""
    return ui.page_fluid(
        ui.tags.link(rel="stylesheet", href="tokens.css"),
        ui.tags.link(rel="stylesheet", href="layout.css"),
        ui.tags.link(rel="stylesheet", href="animations.css"),
        ui.div(
            ui.tags.aside(
                ui.div("流光", class_="brand-mark", aria_hidden="true"),
                ui.div(
                    ui.p("BILIBILI LAB", class_="brand-kicker"),
                    ui.p("流光工作台", class_="brand-name"),
                    class_="brand-copy",
                ),
                ui.p("观察趋势，提炼内容。", class_="side-navigation-note"),
                class_="side-navigation",
                aria_label="应用品牌",
            ),
            ui.tags.nav(
                ui.navset_pill(
                    ui.nav_panel(
                        "排行榜",
                        build_ranking_ui(),
                    ),
                    ui.nav_panel(
                        "视频总结",
                        build_summary_ui(),
                    ),
                    ui.nav_panel(
                        "UP 分析",
                        build_uploader_ui(),
                    ),
                    id="primary_navigation",
                    selected="排行榜",
                ),
                class_="dashboard-navigation",
                aria_label="主要功能",
            ),
            class_="app-shell",
        ),
        title="流光工作台",
        lang="zh-CN",
    )
