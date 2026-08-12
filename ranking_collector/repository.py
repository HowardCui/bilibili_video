#!/usr/bin/env python 3.12
# -*- coding: utf-8 -*-

"""Ranking Collector 的 SQLite 数据持久化函数。"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from ranking_collector.config import DATABASE_PATH
from ranking_collector.models import (
    MetricChange,
    RankingItem,
    RankingSnapshot,
    VideoInfo,
    VideoMetrics,
    validate_datetime,
    validate_text,
)


CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS collection_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    succeeded INTEGER CHECK (succeeded IN (0, 1)),
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS ranking_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    partition TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES collection_runs(id) ON DELETE CASCADE,
    UNIQUE (run_id, partition)
);

CREATE TABLE IF NOT EXISTS ranking_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL,
    bvid TEXT NOT NULL,
    title TEXT NOT NULL,
    uploader TEXT NOT NULL,
    partition TEXT NOT NULL,
    published_at TEXT NOT NULL,
    rank INTEGER NOT NULL CHECK (rank > 0),
    views INTEGER NOT NULL CHECK (views >= 0),
    likes INTEGER NOT NULL CHECK (likes >= 0),
    coins INTEGER NOT NULL CHECK (coins >= 0),
    favorites INTEGER NOT NULL CHECK (favorites >= 0),
    comments INTEGER NOT NULL CHECK (comments >= 0),
    danmaku INTEGER NOT NULL CHECK (danmaku >= 0),
    shares INTEGER NOT NULL CHECK (shares >= 0),
    collected_at TEXT NOT NULL,
    FOREIGN KEY (snapshot_id) REFERENCES ranking_snapshots(id)
        ON DELETE CASCADE,
    UNIQUE (snapshot_id, bvid),
    UNIQUE (snapshot_id, rank)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_partition_time
    ON ranking_snapshots(partition, collected_at DESC);

CREATE INDEX IF NOT EXISTS idx_items_bvid_time
    ON ranking_items(bvid, collected_at DESC);
"""


class RepositoryError(RuntimeError):
    """数据库操作失败或请求的数据不符合仓库约束。"""


def datetime_to_text(value):
    """把带时区时间统一转换成 UTC ISO 8601 字符串。"""
    validate_datetime(value, "datetime")
    return value.astimezone(UTC).isoformat()


def text_to_datetime(value):
    """把数据库中的 ISO 8601 字符串还原成 datetime。"""
    try:
        result = datetime.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise RepositoryError("数据库中包含无效时间") from error
    return validate_datetime(result, "数据库时间")


def connect_database(database_path=DATABASE_PATH):
    """连接 SQLite，并启用外键约束和字典式行访问。"""
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def initialize_database(database_path=DATABASE_PATH):
    """创建数据库、数据表和索引。"""
    with connect_database(database_path) as connection:
        connection.executescript(CREATE_TABLES_SQL)


def create_collection_run(started_at, database_path=DATABASE_PATH):
    """创建一条正在执行的采集任务，返回任务 ID。"""
    started_at_text = datetime_to_text(started_at)

    with connect_database(database_path) as connection:
        cursor = connection.execute(
            "INSERT INTO collection_runs (started_at) VALUES (?)",
            (started_at_text,),
        )
        return cursor.lastrowid


def finish_collection_run(
    run_id,
    succeeded,
    error_message=None,
    finished_at=None,
    database_path=DATABASE_PATH,
):
    """记录采集任务最终是否成功以及失败信息。"""
    if not isinstance(run_id, int) or isinstance(run_id, bool):
        raise TypeError("run_id 必须是整数")
    if run_id < 1:
        raise ValueError("run_id 必须大于 0")
    if not isinstance(succeeded, bool):
        raise TypeError("succeeded 必须是布尔值")

    if succeeded:
        if error_message is not None:
            raise ValueError("成功任务不能包含 error_message")
    else:
        validate_text(error_message, "error_message")

    if finished_at is None:
        finished_at = datetime.now(UTC)
    finished_at_text = datetime_to_text(finished_at)

    with connect_database(database_path) as connection:
        cursor = connection.execute(
            """
            UPDATE collection_runs
            SET finished_at = ?, succeeded = ?, error_message = ?
            WHERE id = ? AND succeeded IS NULL
            """,
            (
                finished_at_text,
                int(succeeded),
                error_message,
                run_id,
            ),
        )

        if cursor.rowcount == 0:
            raise RepositoryError("采集任务不存在或已经结束")


def save_snapshot(run_id, snapshot, database_path=DATABASE_PATH):
    """在一个事务中保存快照及其全部榜单条目。"""
    if not isinstance(run_id, int) or isinstance(run_id, bool):
        raise TypeError("run_id 必须是整数")
    if run_id < 1:
        raise ValueError("run_id 必须大于 0")
    if not isinstance(snapshot, RankingSnapshot):
        raise TypeError("snapshot 必须是 RankingSnapshot")

    collected_at_text = datetime_to_text(snapshot.collected_at)

    try:
        with connect_database(database_path) as connection:
            run = connection.execute(
                "SELECT succeeded FROM collection_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if run is None:
                raise RepositoryError("采集任务不存在")
            if run["succeeded"] is not None:
                raise RepositoryError("不能向已经结束的任务写入快照")

            cursor = connection.execute(
                """
                INSERT INTO ranking_snapshots (
                    run_id,
                    partition,
                    collected_at
                ) VALUES (?, ?, ?)
                """,
                (run_id, snapshot.partition, collected_at_text),
            )
            snapshot_id = cursor.lastrowid

            for item in snapshot.items:
                connection.execute(
                    """
                    INSERT INTO ranking_items (
                        snapshot_id,
                        bvid,
                        title,
                        uploader,
                        partition,
                        published_at,
                        rank,
                        views,
                        likes,
                        coins,
                        favorites,
                        comments,
                        danmaku,
                        shares,
                        collected_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        item.video.bvid,
                        item.video.title,
                        item.video.uploader,
                        item.video.partition,
                        datetime_to_text(item.video.published_at),
                        item.rank,
                        item.metrics.views,
                        item.metrics.likes,
                        item.metrics.coins,
                        item.metrics.favorites,
                        item.metrics.comments,
                        item.metrics.danmaku,
                        item.metrics.shares,
                        datetime_to_text(item.collected_at),
                    ),
                )

            return snapshot_id
    except sqlite3.IntegrityError as error:
        raise RepositoryError(f"快照保存失败：{error}") from error


def row_to_ranking_item(row):
    """把一条数据库记录还原成 RankingItem。"""
    video = VideoInfo(
        bvid=row["bvid"],
        title=row["title"],
        uploader=row["uploader"],
        partition=row["partition"],
        published_at=text_to_datetime(row["published_at"]),
    )
    metrics = VideoMetrics(
        views=row["views"],
        likes=row["likes"],
        coins=row["coins"],
        favorites=row["favorites"],
        comments=row["comments"],
        danmaku=row["danmaku"],
        shares=row["shares"],
    )
    return RankingItem(
        video=video,
        metrics=metrics,
        rank=row["rank"],
        collected_at=text_to_datetime(row["collected_at"]),
    )


def load_snapshot(connection, snapshot_row):
    """按快照数据库记录读取完整榜单。"""
    item_rows = connection.execute(
        """
        SELECT *
        FROM ranking_items
        WHERE snapshot_id = ?
        ORDER BY rank ASC
        """,
        (snapshot_row["id"],),
    ).fetchall()
    items = [row_to_ranking_item(row) for row in item_rows]
    return RankingSnapshot(
        partition=snapshot_row["partition"],
        collected_at=text_to_datetime(snapshot_row["collected_at"]),
        items=items,
    )


def get_latest_successful_snapshot(
    partition,
    before=None,
    database_path=DATABASE_PATH,
):
    """查询指定分区最近一份已经完整写入的快照。"""
    validate_text(partition, "partition")

    parameters = [partition]
    time_condition = ""
    if before is not None:
        time_condition = "AND collected_at < ?"
        parameters.append(datetime_to_text(before))

    with connect_database(database_path) as connection:
        snapshot_row = connection.execute(
            f"""
            SELECT id, partition, collected_at
            FROM ranking_snapshots
            WHERE partition = ? {time_condition}
            ORDER BY collected_at DESC, id DESC
            LIMIT 1
            """,
            parameters,
        ).fetchone()

        if snapshot_row is None:
            return None
        return load_snapshot(connection, snapshot_row)


def get_recent_successful_snapshots(
    partition,
    limit=2,
    before=None,
    database_path=DATABASE_PATH,
):
    """按时间倒序读取指定分区最近若干份完整快照。"""
    validate_text(partition, "partition")
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise TypeError("limit 必须是整数")
    if limit < 1:
        raise ValueError("limit 必须大于 0")

    parameters = [partition]
    time_condition = ""
    if before is not None:
        time_condition = "AND collected_at < ?"
        parameters.append(datetime_to_text(before))
    parameters.append(limit)

    with connect_database(database_path) as connection:
        snapshot_rows = connection.execute(
            f"""
            SELECT id, partition, collected_at
            FROM ranking_snapshots
            WHERE partition = ? {time_condition}
            ORDER BY collected_at DESC, id DESC
            LIMIT ?
            """,
            parameters,
        ).fetchall()
        return [
            load_snapshot(connection, snapshot_row)
            for snapshot_row in snapshot_rows
        ]


def get_video_history(bvid, database_path=DATABASE_PATH):
    """按采集时间顺序查询某个视频的全部历史指标。"""
    validate_text(bvid, "bvid")

    with connect_database(database_path) as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM ranking_items
            WHERE bvid = ?
            ORDER BY collected_at ASC, id ASC
            """,
            (bvid,),
        ).fetchall()
        return [row_to_ranking_item(row) for row in rows]


def get_metric_change(
    bvid,
    partition,
    earlier_collected_at,
    later_collected_at,
    database_path=DATABASE_PATH,
):
    """查询同一视频在同一分区两个精确采集时间点之间的变化。"""
    validate_text(bvid, "bvid")
    validate_text(partition, "partition")
    earlier_text = datetime_to_text(earlier_collected_at)
    later_text = datetime_to_text(later_collected_at)

    if later_collected_at <= earlier_collected_at:
        raise ValueError("later_collected_at 必须晚于 earlier_collected_at")

    with connect_database(database_path) as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM ranking_items
            WHERE bvid = ?
              AND partition = ?
              AND collected_at IN (?, ?)
            ORDER BY collected_at ASC
            """,
            (bvid, partition, earlier_text, later_text),
        ).fetchall()

    items_by_time = {
        row["collected_at"]: row_to_ranking_item(row)
        for row in rows
    }
    earlier_item = items_by_time.get(earlier_text)
    later_item = items_by_time.get(later_text)
    if earlier_item is None or later_item is None:
        return None

    elapsed_hours = (
        later_collected_at - earlier_collected_at
    ).total_seconds() / 3600
    views_delta = later_item.metrics.views - earlier_item.metrics.views

    return MetricChange(
        bvid=bvid,
        views_delta=views_delta,
        likes_delta=later_item.metrics.likes - earlier_item.metrics.likes,
        coins_delta=later_item.metrics.coins - earlier_item.metrics.coins,
        favorites_delta=(
            later_item.metrics.favorites - earlier_item.metrics.favorites
        ),
        comments_delta=(
            later_item.metrics.comments - earlier_item.metrics.comments
        ),
        danmaku_delta=(
            later_item.metrics.danmaku - earlier_item.metrics.danmaku
        ),
        shares_delta=(
            later_item.metrics.shares - earlier_item.metrics.shares
        ),
        rank_change=earlier_item.rank - later_item.rank,
        elapsed_hours=elapsed_hours,
        views_per_hour=views_delta / elapsed_hours,
    )


__all__ = [
    "RepositoryError",
    "connect_database",
    "create_collection_run",
    "finish_collection_run",
    "get_latest_successful_snapshot",
    "get_metric_change",
    "get_recent_successful_snapshots",
    "get_video_history",
    "initialize_database",
    "save_snapshot",
]
