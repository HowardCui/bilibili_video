from datetime import UTC, datetime, timedelta

import pytest

from web_app.trends.service import aggregate_series, build_trend_page_data


def item(bvid, rank, views, **metrics):
    return {
        "bvid": bvid,
        "title": f"Video {bvid}",
        "uploader": f"Uploader {bvid}",
        "rank": rank,
        "views": views,
        "likes": metrics.get("likes", 0),
        "coins": metrics.get("coins", 0),
        "favorites": metrics.get("favorites", 0),
        "comments": metrics.get("comments", 0),
        "danmaku": metrics.get("danmaku", 0),
        "shares": metrics.get("shares", 0),
    }


def snapshot(collected_at, items):
    return {
        "snapshot_id": int(collected_at.timestamp()),
        "collected_at": collected_at,
        "items": items,
    }


def history_fixture():
    start = datetime(2026, 8, 1, tzinfo=UTC)
    times = [
        start,
        start + timedelta(hours=6),
        start + timedelta(hours=12),
        start + timedelta(hours=38),
    ]
    return {
        "partition": "全站",
        "range_key": "ALL",
        "started_at": None,
        "ended_at": times[-1],
        "truncated": False,
        "snapshots": [
            snapshot(times[0], [item("A", 3, 100), item("B", 1, 300)]),
            snapshot(times[1], [item("A", 2, 160)]),
            snapshot(times[2], [item("B", 2, 350)]),
            snapshot(times[3], [item("A", 1, 140), item("C", 2, 80)]),
        ],
        "partition_results": [
            {"collected_at": times[2] + timedelta(hours=1), "succeeded": False}
        ],
    }


def test_video_summary_tracks_presence_ranks_and_reentry():
    page = build_trend_page_data(history_fixture(), selected_bvid="A")

    assert page["selected_bvid"] == "A"
    assert page["video_summary"] == {
        "bvid": "A",
        "title": "Video A",
        "uploader": "Uploader A",
        "first_ranked_at": datetime(2026, 8, 1, tzinfo=UTC),
        "last_ranked_at": datetime(2026, 8, 2, 14, tzinfo=UTC),
        "consecutive_count": 1,
        "cumulative_count": 3,
        "best_rank": 1,
        "worst_rank": 3,
        "current_rank": 1,
        "reentry_count": 1,
    }
    assert [point["value"] for point in page["rank_series"]] == [3, 2, None, 1]
    assert [point["value"] for point in page["metric_series"]] == [
        100,
        160,
        None,
        140,
    ]


def test_partition_series_preserve_stale_turnover_heat_and_failures():
    page = build_trend_page_data(history_fixture(), selected_bvid="A")

    assert [point["value"] for point in page["turnover_series"]] == [
        None,
        0.5,
        1.0,
        None,
    ]
    assert page["turnover_series"][-1]["status"] == "STALE"
    assert [point["value"] for point in page["heat_series"]] == [400, 160, 350, 220]
    assert len(page["missing_intervals"]) == 2
    assert page["metadata"]["snapshot_count"] == 4
    assert page["metadata"]["latest_snapshot_at"] == datetime(
        2026, 8, 2, 14, tzinfo=UTC
    )


def test_default_video_is_latest_highest_rank_and_lists_are_stable():
    page = build_trend_page_data(history_fixture())

    assert page["selected_bvid"] == "A"
    assert [choice["bvid"] for choice in page["video_choices"]] == ["A", "C", "B"]
    assert page["lists"]["long_running"][0]["bvid"] == "A"
    assert page["lists"]["reentries"] == [
        {
            "bvid": "A",
            "title": "Video A",
            "at": datetime(2026, 8, 2, 14, tzinfo=UTC),
        },
        {
            "bvid": "B",
            "title": "Video B",
            "at": datetime(2026, 8, 1, 12, tzinfo=UTC),
        },
    ]


def test_aggregate_series_limits_points_and_validates_mode():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    points = [
        {"at": start + timedelta(minutes=index), "value": index} for index in range(500)
    ]

    aggregated = aggregate_series(points, 240, "last")

    assert len(aggregated) <= 240
    assert aggregated[-1] == points[-1]
    with pytest.raises(ValueError, match="mode"):
        aggregate_series(points, 240, "median")


def test_insufficient_history_has_explicit_status():
    history = history_fixture()
    history["snapshots"] = history["snapshots"][:1]

    page = build_trend_page_data(history)

    assert page["status"] == "INSUFFICIENT_DATA"
