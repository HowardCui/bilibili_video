import sqlite3

from ranking_collector.config import PARTITIONS
from ranking_collector.repository import initialize_database
from web_app.ranking.ui import build_ranking_ui
from web_app.trends import server as trend_server

ALL_PARTITION = PARTITIONS["all"]["name"]


def test_ranking_ui_mounts_long_term_trend_controls():
    rendered = str(build_ranking_ui())

    assert "长期趋势" in rendered
    assert "trend_range" in rendered
    assert "trend_rank_chart" in rendered


def test_load_trend_page_data_returns_explicit_no_data(tmp_path):
    database_path = tmp_path / "ranking.db"
    initialize_database(database_path)

    page = trend_server.load_trend_page_data(
        ALL_PARTITION,
        "7D",
        None,
        "views",
        database_path,
    )

    assert page["status"] == "NO_DATA"
    assert page["partition"] == ALL_PARTITION
    assert page["video_choices"] == []


def test_load_trend_page_data_maps_storage_failure_without_details(
    monkeypatch,
    tmp_path,
):
    def fail_query(*_args, **_kwargs):
        raise sqlite3.OperationalError("database at C:/private/path is locked")

    monkeypatch.setattr(trend_server, "load_partition_history", fail_query)

    page = trend_server.load_trend_page_data(
        ALL_PARTITION,
        "7D",
        None,
        "views",
        tmp_path / "ranking.db",
    )

    assert page == {
        "status": "QUERY_FAILED",
        "partition": ALL_PARTITION,
        "range_key": "7D",
        "metric": "views",
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
