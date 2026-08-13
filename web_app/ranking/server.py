"""Shiny server bindings for the read-only ranking dashboard."""

import sqlite3

from shiny import reactive, render, ui

from ranking_collector.repository import RepositoryError

from .queries import build_ranking_page_data
from .ui import (
    ranking_view_model,
    render_ranking_changes,
    render_ranking_metrics,
    render_ranking_table,
)


def load_ranking_page_data(partition, database_path) -> dict:
    """Map expected read failures to one stable, non-sensitive page state."""
    try:
        return build_ranking_page_data(partition, database_path)
    except (RepositoryError, sqlite3.Error, OSError):
        return {
            "partition": partition,
            "collected_at": None,
            "collection_status": "QUERY_FAILED",
            "items": [],
            "comparison": None,
        }


def register_ranking_server(input, output, session, database_path) -> None:
    """Register ranking outputs without starting any collection work."""
    _ = session

    @reactive.calc
    def page_data():
        return load_ranking_page_data(input.ranking_partition(), database_path)

    @reactive.calc
    def ranking_view():
        return ranking_view_model(page_data())

    @render.text
    def ranking_snapshot():
        return ranking_view()["snapshot_time"]

    @render.text
    def ranking_freshness():
        return ranking_view()["freshness"]

    @render.ui
    def ranking_empty():
        view = ranking_view()
        if not view["empty"]:
            return None
        return ui.p(view["empty_message"], class_="ranking-empty")

    @render.ui
    def ranking_metrics():
        return render_ranking_metrics(ranking_view()["metric_summary"])

    @render.ui
    def ranking_changes():
        return render_ranking_changes(ranking_view()["changes"])

    @render.ui
    def ranking_top_100():
        return render_ranking_table(ranking_view()["items"])


__all__ = ["load_ranking_page_data", "register_ranking_server"]
