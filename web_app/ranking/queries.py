"""Read-only ranking queries converted into page-safe dictionaries."""

from pathlib import Path

from ranking_collector.models import ComparisonSource, RankingItem
from ranking_collector.repository import (
    get_enabled_partition_names,
    get_ranking_page_context,
    validate_enabled_partition,
)
from ranking_collector.service import compare_snapshots


def list_partitions(database_path: str | Path) -> list[str]:
    """Return all configured enabled partitions, including empty ones."""
    _ = database_path
    return get_enabled_partition_names()


def ranking_item_to_page_data(item: RankingItem) -> dict[str, object]:
    """Convert one ranking item to JSON- and template-safe primitive values."""
    if not isinstance(item, RankingItem):
        raise TypeError("item must be a RankingItem")
    return {
        "rank": item.rank,
        "bvid": item.video.bvid,
        "title": item.video.title,
        "uploader": item.video.uploader,
        "partition": item.video.partition,
        "published_at": item.video.published_at.isoformat(),
        "collected_at": item.collected_at.isoformat(),
        "views": item.metrics.views,
        "likes": item.metrics.likes,
        "coins": item.metrics.coins,
        "favorites": item.metrics.favorites,
        "comments": item.metrics.comments,
        "danmaku": item.metrics.danmaku,
        "shares": item.metrics.shares,
    }


def metric_change_to_page_data(change) -> dict[str, object]:
    """Convert one calculated metric change to primitive page values."""
    return {
        "bvid": change.bvid,
        "views_delta": change.views_delta,
        "likes_delta": change.likes_delta,
        "coins_delta": change.coins_delta,
        "favorites_delta": change.favorites_delta,
        "comments_delta": change.comments_delta,
        "danmaku_delta": change.danmaku_delta,
        "shares_delta": change.shares_delta,
        "rank_change": change.rank_change,
        "elapsed_hours": change.elapsed_hours,
        "views_per_hour": change.views_per_hour,
    }


def comparison_to_page_data(comparison) -> dict[str, object]:
    """Serialize existing comparison results without changing their semantics."""
    return {
        "partition": comparison.partition,
        "previous_collected_at": (
            comparison.previous_collected_at.isoformat()
            if comparison.previous_collected_at is not None
            else None
        ),
        "current_collected_at": comparison.current_collected_at.isoformat(),
        "elapsed_hours": comparison.elapsed_hours,
        "status": comparison.status,
        "source": comparison.source,
        "retained": [
            ranking_item_to_page_data(item) for item in comparison.retained
        ],
        "entered": [
            ranking_item_to_page_data(item) for item in comparison.entered
        ],
        "exited": [
            ranking_item_to_page_data(item) for item in comparison.exited
        ],
        "metric_changes": [
            metric_change_to_page_data(change)
            for change in comparison.metric_changes
        ],
        "ranking_risers": [
            metric_change_to_page_data(change)
            for change in comparison.ranking_risers
        ],
        "views_growth_ranking": [
            metric_change_to_page_data(change)
            for change in comparison.views_growth_ranking
        ],
        "turnover_rate": comparison.turnover_rate,
    }


def build_ranking_page_data(
    partition: str,
    database_path: str | Path,
) -> dict[str, object]:
    """Build the latest read-only page model for one enabled partition."""
    validate_enabled_partition(partition)
    context = get_ranking_page_context(partition, database_path)
    collection_result = context["collection_result"]
    recent_snapshots = context["snapshots"]
    current_snapshot = recent_snapshots[0] if recent_snapshots else None
    if current_snapshot is None:
        if collection_result is not None and not collection_result["succeeded"]:
            return {
                "partition": partition,
                "collected_at": None,
                "collection_status": "FAILED",
                "items": [],
                "comparison": None,
                "collection_failed_at": collection_result[
                    "collected_at"
                ].isoformat(),
                "collection_error": collection_result["error_message"],
            }
        return {
            "partition": partition,
            "collected_at": None,
            "collection_status": "NO_DATA",
            "items": [],
            "comparison": None,
        }

    previous_snapshot = (
        recent_snapshots[1] if len(recent_snapshots) == 2 else None
    )
    source = ComparisonSource.CURRENT
    collection_status = "CURRENT"
    if collection_result is not None and not collection_result["succeeded"]:
        source = ComparisonSource.LAST_VALID
        collection_status = "LAST_VALID"
    comparison = compare_snapshots(
        previous_snapshot,
        current_snapshot,
        source=source,
    )
    page = {
        "partition": partition,
        "collected_at": current_snapshot.collected_at.isoformat(),
        "collection_status": collection_status,
        "items": [
            ranking_item_to_page_data(item)
            for item in sorted(current_snapshot.items, key=lambda item: item.rank)
        ],
        "comparison": comparison_to_page_data(comparison),
    }
    if collection_status == "LAST_VALID":
        page["collection_failed_at"] = collection_result[
            "collected_at"
        ].isoformat()
        page["collection_error"] = collection_result["error_message"]
    return page


__all__ = [
    "build_ranking_page_data",
    "comparison_to_page_data",
    "list_partitions",
]
