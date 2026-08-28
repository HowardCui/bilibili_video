"""Bounded read-only queries for ranking trend facts."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from ranking_collector.repository import (
    connect_database,
    text_to_datetime,
    validate_enabled_partition,
)

RANGE_DELTAS = {
    "24H": timedelta(hours=24),
    "7D": timedelta(days=7),
    "30D": timedelta(days=30),
    "ALL": None,
}


def resolve_time_range(range_key: str, now: datetime) -> datetime | None:
    """Resolve one supported range key to an inclusive UTC start time."""
    if range_key not in RANGE_DELTAS:
        raise ValueError("range_key is not supported")
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("now must be a timezone-aware datetime")
    delta = RANGE_DELTAS[range_key]
    if delta is None:
        return None
    return now.astimezone(UTC) - delta


def _validate_row_limit(row_limit):
    if not isinstance(row_limit, int) or isinstance(row_limit, bool):
        raise TypeError("row_limit must be an integer")
    if not 1 <= row_limit <= 100_000:
        raise ValueError("row_limit must be between 1 and 100000")
    return row_limit


def _snapshot_rows(connection, partition, started_at, ended_at, limit):
    filters = ["partition = ?", "collected_at <= ?"]
    parameters = [partition, ended_at.isoformat()]
    if started_at is not None:
        filters.append("collected_at >= ?")
        parameters.append(started_at.isoformat())
    parameters.append(limit + 1)
    return connection.execute(
        f"""
        SELECT id, collected_at
        FROM ranking_snapshots
        WHERE {" AND ".join(filters)}
        ORDER BY collected_at ASC, id ASC
        LIMIT ?
        """,
        parameters,
    ).fetchall()


def _snapshot_items(connection, snapshot_id):
    rows = connection.execute(
        """
        SELECT bvid, title, uploader, rank, views, likes, coins,
               favorites, comments, danmaku, shares, collected_at
        FROM ranking_items
        WHERE snapshot_id = ?
        ORDER BY rank ASC
        """,
        (snapshot_id,),
    ).fetchall()
    return [
        {
            "bvid": row["bvid"],
            "title": row["title"],
            "uploader": row["uploader"],
            "rank": row["rank"],
            "views": row["views"],
            "likes": row["likes"],
            "coins": row["coins"],
            "favorites": row["favorites"],
            "comments": row["comments"],
            "danmaku": row["danmaku"],
            "shares": row["shares"],
            "collected_at": text_to_datetime(row["collected_at"]),
        }
        for row in rows
    ]


def _partition_results(connection, partition, started_at, ended_at):
    filters = ["partition = ?", "collected_at <= ?", "succeeded = 0"]
    parameters = [partition, ended_at.isoformat()]
    if started_at is not None:
        filters.append("collected_at >= ?")
        parameters.append(started_at.isoformat())
    rows = connection.execute(
        f"""
        SELECT collected_at, succeeded
        FROM collection_partition_results
        WHERE {" AND ".join(filters)}
        ORDER BY collected_at ASC, id ASC
        """,
        parameters,
    ).fetchall()
    return [
        {
            "collected_at": text_to_datetime(row["collected_at"]),
            "succeeded": bool(row["succeeded"]),
        }
        for row in rows
    ]


def load_partition_history(
    partition: str,
    range_key: str,
    database_path: str | Path,
    now: datetime | None = None,
    row_limit: int = 50_000,
) -> dict:
    """Load ordered snapshot facts and collection outcomes for one range."""
    validate_enabled_partition(partition)
    _validate_row_limit(row_limit)
    ended_at = now or datetime.now(UTC)
    if ended_at.tzinfo is None:
        raise ValueError("now must be a timezone-aware datetime")
    ended_at = ended_at.astimezone(UTC)
    started_at = resolve_time_range(range_key, ended_at)

    with connect_database(database_path) as connection:
        rows = _snapshot_rows(
            connection,
            partition,
            started_at,
            ended_at,
            row_limit,
        )
        truncated = len(rows) > row_limit
        rows = rows[:row_limit]
        snapshots = [
            {
                "snapshot_id": row["id"],
                "collected_at": text_to_datetime(row["collected_at"]),
                "items": _snapshot_items(connection, row["id"]),
            }
            for row in rows
        ]
        partition_results = _partition_results(
            connection,
            partition,
            started_at,
            ended_at,
        )
    return {
        "partition": partition,
        "range_key": range_key,
        "started_at": started_at,
        "ended_at": ended_at,
        "snapshots": snapshots,
        "partition_results": partition_results,
        "truncated": truncated,
    }


__all__ = ["load_partition_history", "resolve_time_range"]
