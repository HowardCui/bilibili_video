"""Ranking Collector 快照比较的最小纯函数测试。"""

from datetime import UTC, datetime, timedelta

from ranking_collector.config import TOP_N
from ranking_collector.models import (
    ComparisonSource,
    ComparisonStatus,
    RankingItem,
    RankingSnapshot,
    VideoInfo,
    VideoMetrics,
)
from ranking_collector.service import compare_snapshots


def make_item(bvid, rank, views, collected_at):
    video = VideoInfo(
        bvid=bvid,
        title=f"视频 {bvid}",
        uploader=f"UP {bvid}",
        partition="知识",
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    metrics = VideoMetrics(views=views)
    return RankingItem(video, metrics, rank, collected_at)


def make_snapshot(collected_at, rows):
    items = [
        make_item(bvid, rank, views, collected_at)
        for bvid, rank, views in rows
    ]
    return RankingSnapshot("知识", collected_at, items)


def test_partial_overlap():
    previous_at = datetime(2026, 1, 1, tzinfo=UTC)
    current_at = previous_at + timedelta(hours=6)
    previous = make_snapshot(
        previous_at,
        [("A", 1, 100), ("B", 3, 100)],
    )
    current = make_snapshot(
        current_at,
        [("B", 1, 220), ("C", 2, 50)],
    )

    result = compare_snapshots(previous, current)

    assert result.status == ComparisonStatus.VALID
    assert [item.video.bvid for item in result.retained] == ["B"]
    assert [item.video.bvid for item in result.entered] == ["C"]
    assert [item.video.bvid for item in result.exited] == ["A"]
    assert result.turnover_rate == 0.5
    assert [change.bvid for change in result.ranking_risers] == ["B"]
    assert [change.bvid for change in result.views_growth_ranking] == ["B"]


def test_more_than_24_hours_disables_turnover():
    previous_at = datetime(2026, 1, 1, tzinfo=UTC)
    current_at = previous_at + timedelta(hours=25)
    previous = make_snapshot(previous_at, [("A", 2, 100)])
    current = make_snapshot(current_at, [("A", 1, 200)])

    result = compare_snapshots(previous, current)

    assert result.status == ComparisonStatus.STALE
    assert result.turnover_rate is None
    assert result.metric_changes[0].views_delta == 100


def test_empty_current_has_independent_status():
    previous_at = datetime(2026, 1, 1, tzinfo=UTC)
    current_at = previous_at + timedelta(hours=6)
    previous = make_snapshot(previous_at, [("A", 1, 100)])
    current = make_snapshot(current_at, [])

    result = compare_snapshots(previous, current)

    assert result.status == ComparisonStatus.EMPTY_CURRENT
    assert result.exited == []
    assert result.turnover_rate is None


def test_no_baseline_and_last_valid_source():
    previous_at = datetime(2026, 1, 1, tzinfo=UTC)
    current_at = previous_at + timedelta(hours=6)
    current = make_snapshot(current_at, [("A", 1, 100)])

    no_baseline = compare_snapshots(None, current)
    assert no_baseline.status == ComparisonStatus.NO_BASELINE
    assert no_baseline.entered == []

    previous = make_snapshot(previous_at, [("A", 2, 50)])
    last_valid = compare_snapshots(
        previous,
        current,
        source=ComparisonSource.LAST_VALID,
    )
    assert last_valid.source == ComparisonSource.LAST_VALID


def test_collection_limit_is_100():
    assert TOP_N == 100


def run_all_tests():
    test_partial_overlap()
    test_more_than_24_hours_disables_turnover()
    test_empty_current_has_independent_status()
    test_no_baseline_and_last_valid_source()
    test_collection_limit_is_100()
    print("5 ranking collector tests passed")


if __name__ == "__main__":
    run_all_tests()
