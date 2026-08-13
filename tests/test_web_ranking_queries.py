import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from ranking_collector.config import PARTITIONS
from ranking_collector.models import (
    ComparisonSource,
    RankingItem,
    RankingSnapshot,
    VideoInfo,
    VideoMetrics,
)
from ranking_collector.repository import (
    RepositoryError,
    create_collection_run,
    finish_collection_run,
    get_ranking_page_context,
    get_snapshot_by_id,
    initialize_database,
    list_snapshot_summaries,
    save_snapshot,
    save_successful_partition_snapshot,
)
from ranking_collector.service import collect_once, compare_snapshots
from web_app.app import create_app
from web_app.ranking.queries import (
    build_ranking_page_data,
    comparison_to_page_data,
    list_partitions,
)

ALL_PARTITION = PARTITIONS["all"]["name"]
TECH_PARTITION = PARTITIONS["tech"]["name"]


def make_snapshot(partition, collected_at, rows):
    items = []
    for rank, bvid, views in rows:
        items.append(
            RankingItem(
                VideoInfo(
                    bvid=bvid,
                    title=f"Video {bvid}",
                    uploader=f"Uploader {bvid}",
                    partition=partition,
                    published_at=datetime(2026, 1, 1, tzinfo=UTC),
                ),
                VideoMetrics(views=views, likes=rank),
                rank,
                collected_at,
            )
        )
    return RankingSnapshot(partition, collected_at, items)


def save_completed_snapshot(database_path, snapshot):
    run_id = create_collection_run(snapshot.collected_at, database_path)
    snapshot_id = save_snapshot(run_id, snapshot, database_path)
    finish_collection_run(
        run_id,
        succeeded=True,
        finished_at=snapshot.collected_at,
        database_path=database_path,
    )
    return snapshot_id


def test_empty_partition_returns_no_data(tmp_path):
    database_path = tmp_path / "ranking.db"
    initialize_database(database_path)

    page = build_ranking_page_data(ALL_PARTITION, database_path)

    assert page == {
        "partition": ALL_PARTITION,
        "collected_at": None,
        "collection_status": "NO_DATA",
        "items": [],
        "comparison": None,
    }


def test_page_items_are_rank_sorted_and_page_safe(tmp_path):
    database_path = tmp_path / "ranking.db"
    initialize_database(database_path)
    collected_at = datetime(2026, 1, 2, tzinfo=UTC)
    snapshot = make_snapshot(
        TECH_PARTITION,
        collected_at,
        [(2, "BV2", 20), (1, "BV1", 10)],
    )
    save_completed_snapshot(database_path, snapshot)

    page = build_ranking_page_data(TECH_PARTITION, database_path)

    assert page["collection_status"] == "CURRENT"
    assert page["collected_at"] == collected_at.isoformat()
    assert [item["rank"] for item in page["items"]] == [1, 2]
    assert page["items"][0] == {
        "rank": 1,
        "bvid": "BV1",
        "title": "Video BV1",
        "uploader": "Uploader BV1",
        "partition": TECH_PARTITION,
        "published_at": "2026-01-01T00:00:00+00:00",
        "collected_at": collected_at.isoformat(),
        "views": 10,
        "likes": 1,
        "coins": 0,
        "favorites": 0,
        "comments": 0,
        "danmaku": 0,
        "shares": 0,
    }


def test_snapshot_summaries_and_exact_lookup_are_read_only(tmp_path):
    database_path = tmp_path / "ranking.db"
    initialize_database(database_path)
    earlier_at = datetime(2026, 1, 1, tzinfo=UTC)
    later_at = earlier_at + timedelta(hours=6)
    earlier_id = save_completed_snapshot(
        database_path,
        make_snapshot(ALL_PARTITION, earlier_at, [(1, "BV1", 10)]),
    )
    later_id = save_completed_snapshot(
        database_path,
        make_snapshot(ALL_PARTITION, later_at, [(1, "BV1", 20), (2, "BV2", 5)]),
    )

    summaries = list_snapshot_summaries(ALL_PARTITION, 1, database_path)
    snapshot = get_snapshot_by_id(earlier_id, database_path)

    assert summaries == [
        {
            "id": later_id,
            "partition": ALL_PARTITION,
            "collected_at": later_at.isoformat(),
            "item_count": 2,
        }
    ]
    assert snapshot is not None
    assert snapshot.partition == ALL_PARTITION
    assert [item.video.bvid for item in snapshot.items] == ["BV1"]
    assert get_snapshot_by_id(999_999, database_path) is None


def test_invalid_partitions_limits_and_ids_fail_clearly(tmp_path):
    database_path = tmp_path / "ranking.db"
    initialize_database(database_path)

    with pytest.raises(ValueError, match="partition"):
        build_ranking_page_data("not-a-partition", database_path)
    with pytest.raises(ValueError, match="partition"):
        list_snapshot_summaries("' OR 1=1 --", 1, database_path)
    with pytest.raises(ValueError, match="limit"):
        list_snapshot_summaries(ALL_PARTITION, 0, database_path)
    with pytest.raises(TypeError, match="snapshot_id"):
        get_snapshot_by_id("1", database_path)
    with pytest.raises(ValueError, match="snapshot_id"):
        get_snapshot_by_id(0, database_path)


def test_enabled_partitions_are_available_without_snapshots(tmp_path):
    database_path = tmp_path / "ranking.db"
    initialize_database(database_path)

    assert list_partitions(database_path) == [
        partition["name"]
        for partition in PARTITIONS.values()
        if partition["enabled"]
    ]


def test_comparison_states_preserve_source_and_status(tmp_path):
    database_path = tmp_path / "ranking.db"
    initialize_database(database_path)
    baseline_at = datetime(2026, 1, 1, tzinfo=UTC)
    no_baseline = make_snapshot(ALL_PARTITION, baseline_at, [(1, "BV1", 10)])
    current = make_snapshot(
        ALL_PARTITION,
        baseline_at + timedelta(hours=6),
        [(1, "BV1", 20), (2, "BV2", 5)],
    )
    stale = make_snapshot(
        ALL_PARTITION,
        baseline_at + timedelta(hours=25),
        [(1, "BV1", 30)],
    )
    empty = make_snapshot(ALL_PARTITION, baseline_at + timedelta(hours=6), [])

    valid_page_comparison = comparison_to_page_data(
        compare_snapshots(no_baseline, current)
    )
    last_valid_page_comparison = comparison_to_page_data(
        compare_snapshots(
            no_baseline,
            current,
            source=ComparisonSource.LAST_VALID,
        )
    )
    no_baseline_page_comparison = comparison_to_page_data(
        compare_snapshots(None, no_baseline)
    )
    stale_page_comparison = comparison_to_page_data(
        compare_snapshots(no_baseline, stale)
    )
    empty_page_comparison = comparison_to_page_data(
        compare_snapshots(no_baseline, empty)
    )

    assert valid_page_comparison["source"] == "CURRENT"
    assert valid_page_comparison["status"] == "VALID"
    assert last_valid_page_comparison["source"] == "LAST_VALID"
    assert no_baseline_page_comparison["status"] == "NO_BASELINE"
    assert stale_page_comparison["status"] == "STALE"
    assert stale_page_comparison["turnover_rate"] is None
    assert empty_page_comparison["status"] == "EMPTY_CURRENT"
    assert empty_page_comparison["exited"] == []


def test_failed_latest_partition_uses_last_valid_snapshot(tmp_path):
    database_path = tmp_path / "ranking.db"
    partitions = {
        "all": {
            "name": ALL_PARTITION,
            "rid": 0,
            "enabled": True,
        }
    }
    initial_at = datetime(2026, 1, 1, tzinfo=UTC)
    later_at = initial_at + timedelta(hours=6)
    failed_at = later_at + timedelta(hours=6)

    def successful_fetch(*_args, **_kwargs):
        return [
            {
                "bvid": "BV1",
                "title": "Video BV1",
                "owner": {"name": "Uploader BV1"},
                "pubdate": 1_704_067_200,
                "stat": {"view": 10},
            }
        ]

    def failing_fetch(*_args, **_kwargs):
        raise RuntimeError("Bearer credential must not reach the page")

    collect_once(
        collected_at=initial_at,
        fetch_function=successful_fetch,
        database_path=database_path,
        partitions=partitions,
    )
    collect_once(
        collected_at=later_at,
        fetch_function=successful_fetch,
        database_path=database_path,
        partitions=partitions,
    )
    collect_once(
        collected_at=failed_at,
        fetch_function=failing_fetch,
        database_path=database_path,
        partitions=partitions,
    )

    page = build_ranking_page_data(ALL_PARTITION, database_path)

    assert page["collection_status"] == "LAST_VALID"
    assert page["collected_at"] == later_at.isoformat()
    assert page["collection_failed_at"] == failed_at.isoformat()
    assert page["collection_error"] == "Collection failed"
    assert page["comparison"]["source"] == "LAST_VALID"
    assert page["comparison"]["current_collected_at"] == later_at.isoformat()


def test_page_query_uses_one_snapshot_context_during_new_write(
    tmp_path,
    monkeypatch,
):
    database_path = tmp_path / "ranking.db"
    initialize_database(database_path)
    first_at = datetime(2026, 1, 1, tzinfo=UTC)
    second_at = first_at + timedelta(hours=6)
    third_at = second_at + timedelta(hours=6)
    save_completed_snapshot(
        database_path,
        make_snapshot(ALL_PARTITION, first_at, [(1, "BV1", 10)]),
    )
    save_completed_snapshot(
        database_path,
        make_snapshot(ALL_PARTITION, second_at, [(1, "BV1", 20)]),
    )

    original_context_query = get_ranking_page_context

    def context_then_write(partition, path):
        context = original_context_query(partition, path)
        save_completed_snapshot(
            path,
            make_snapshot(ALL_PARTITION, third_at, [(1, "BV1", 30)]),
        )
        return context

    monkeypatch.setattr(
        "web_app.ranking.queries.get_ranking_page_context",
        context_then_write,
    )

    page = build_ranking_page_data(ALL_PARTITION, database_path)

    assert page["collected_at"] == second_at.isoformat()
    assert page["comparison"]["previous_collected_at"] == first_at.isoformat()


def test_collection_keeps_supporting_caller_enabled_partitions(tmp_path):
    database_path = tmp_path / "ranking.db"
    collected_at = datetime(2026, 1, 1, tzinfo=UTC)
    partitions = {
        "custom": {
            "name": "Custom partition",
            "rid": 42,
            "enabled": True,
        }
    }

    result = collect_once(
        collected_at=collected_at,
        fetch_function=lambda *_args, **_kwargs: [],
        database_path=database_path,
        partitions=partitions,
    )

    assert result["succeeded"] is True
    assert result["partitions"][0]["name"] == "Custom partition"


def test_app_creation_migrates_legacy_database_before_page_reads(tmp_path):
    database_path = tmp_path / "ranking.db"
    initialize_database(database_path)
    collected_at = datetime(2026, 1, 1, tzinfo=UTC)
    save_completed_snapshot(
        database_path,
        make_snapshot(ALL_PARTITION, collected_at, [(1, "BV1", 10)]),
    )
    with sqlite3.connect(database_path) as connection:
        connection.execute("DROP TABLE collection_partition_results")

    create_app(database_path=database_path)

    page = build_ranking_page_data(ALL_PARTITION, database_path)
    assert page["collection_status"] == "CURRENT"
    assert page["collected_at"] == collected_at.isoformat()


def test_successful_partition_save_rolls_back_snapshot_with_outcome(tmp_path):
    database_path = tmp_path / "ranking.db"
    initialize_database(database_path)
    first_at = datetime(2026, 1, 1, tzinfo=UTC)
    first_run_id = create_collection_run(first_at, database_path)
    first_snapshot = make_snapshot(
        ALL_PARTITION,
        first_at,
        [(1, "BV1", 10)],
    )
    save_successful_partition_snapshot(
        first_run_id,
        first_snapshot,
        database_path,
    )
    visible_context = get_ranking_page_context(ALL_PARTITION, database_path)
    assert visible_context["collection_result"]["succeeded"] is True
    assert [
        item.video.bvid for item in visible_context["snapshots"][0].items
    ] == ["BV1"]

    second_at = first_at + timedelta(hours=6)
    second_run_id = create_collection_run(second_at, database_path)
    second_snapshot = make_snapshot(
        ALL_PARTITION,
        second_at,
        [(1, "BV1", 20)],
    )
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_partition_success
            BEFORE INSERT ON collection_partition_results
            WHEN NEW.succeeded = 1
            BEGIN
                SELECT RAISE(ABORT, 'reject success outcome');
            END
            """
        )

    with pytest.raises(RepositoryError, match="reject success outcome"):
        save_successful_partition_snapshot(
            second_run_id,
            second_snapshot,
            database_path,
        )

    context = get_ranking_page_context(ALL_PARTITION, database_path)
    assert context["collection_result"]["succeeded"] is True
    assert [
        item.collected_at for item in context["snapshots"]
    ] == [first_at]
