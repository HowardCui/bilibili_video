from datetime import UTC, datetime, timedelta

import pytest

from ranking_collector.config import PARTITIONS
from ranking_collector.models import (
    RankingItem,
    RankingSnapshot,
    VideoInfo,
    VideoMetrics,
)
from ranking_collector.repository import (
    create_collection_run,
    finish_collection_run,
    initialize_database,
    record_partition_collection_result,
    save_successful_partition_snapshot,
)
from web_app.trends.queries import load_partition_history, resolve_time_range

ALL_PARTITION = PARTITIONS["all"]["name"]
TECH_PARTITION = PARTITIONS["tech"]["name"]


def make_snapshot(partition, collected_at, bvid, views):
    item = RankingItem(
        VideoInfo(
            bvid,
            f"Video {bvid}",
            f"Uploader {bvid}",
            partition,
            datetime(2026, 1, 1, tzinfo=UTC),
        ),
        VideoMetrics(views=views),
        1,
        collected_at,
    )
    return RankingSnapshot(partition, collected_at, [item])


def save_partition_snapshot(database_path, snapshot):
    run_id = create_collection_run(snapshot.collected_at, database_path)
    save_successful_partition_snapshot(run_id, snapshot, database_path)
    finish_collection_run(
        run_id,
        True,
        finished_at=snapshot.collected_at,
        database_path=database_path,
    )


def test_resolve_time_range_supports_fixed_ranges_and_all():
    now = datetime(2026, 8, 28, 12, tzinfo=UTC)

    assert resolve_time_range("24H", now) == now - timedelta(hours=24)
    assert resolve_time_range("7D", now) == now - timedelta(days=7)
    assert resolve_time_range("30D", now) == now - timedelta(days=30)
    assert resolve_time_range("ALL", now) is None
    with pytest.raises(ValueError, match="range_key"):
        resolve_time_range("YEAR", now)


def test_load_partition_history_filters_orders_and_preserves_failures(tmp_path):
    database_path = tmp_path / "ranking.db"
    initialize_database(database_path)
    now = datetime(2026, 8, 28, 12, tzinfo=UTC)
    earlier = now - timedelta(days=2)
    recent = now - timedelta(hours=2)
    save_partition_snapshot(
        database_path, make_snapshot(ALL_PARTITION, earlier, "BVOLD", 10)
    )
    save_partition_snapshot(
        database_path, make_snapshot(ALL_PARTITION, recent, "BVNEW", 20)
    )
    save_partition_snapshot(
        database_path, make_snapshot(TECH_PARTITION, recent, "BVTECH", 30)
    )
    failed_at = now - timedelta(hours=1)
    run_id = create_collection_run(failed_at, database_path)
    record_partition_collection_result(
        run_id,
        ALL_PARTITION,
        failed_at,
        False,
        "safe failure",
        database_path,
    )
    finish_collection_run(
        run_id,
        False,
        "safe failure",
        failed_at,
        database_path,
    )

    history = load_partition_history(
        ALL_PARTITION,
        "24H",
        database_path,
        now=now,
    )

    assert [row["bvid"] for row in history["snapshots"][0]["items"]] == ["BVNEW"]
    assert history["partition_results"] == [
        {
            "collected_at": failed_at,
            "succeeded": False,
        }
    ]
    assert history["started_at"] == now - timedelta(hours=24)
    assert history["ended_at"] == now
    assert history["truncated"] is False


def test_load_partition_history_reports_row_limit(tmp_path):
    database_path = tmp_path / "ranking.db"
    initialize_database(database_path)
    now = datetime(2026, 8, 28, 12, tzinfo=UTC)
    for offset in range(3):
        collected_at = now - timedelta(hours=offset)
        save_partition_snapshot(
            database_path,
            make_snapshot(ALL_PARTITION, collected_at, f"BV{offset}", offset),
        )

    history = load_partition_history(
        ALL_PARTITION,
        "ALL",
        database_path,
        now=now,
        row_limit=2,
    )

    assert history["truncated"] is True
    assert len(history["snapshots"]) == 2
