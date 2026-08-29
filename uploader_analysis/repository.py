"""UP 档案、采集任务、历史投稿和指标快照持久化。"""

import sqlite3
from datetime import UTC, datetime

from ranking_collector.config import DATABASE_PATH
from ranking_collector.repository import connect_database, datetime_to_text

UPLOADER_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS uploader_profiles (
    uploader_id INTEGER PRIMARY KEY,
    current_name TEXT NOT NULL,
    first_ranked_at TEXT NOT NULL,
    last_ranked_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS uploader_collection_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uploader_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    cursor INTEGER NOT NULL DEFAULT 1,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT,
    error_code TEXT,
    FOREIGN KEY (uploader_id) REFERENCES uploader_profiles(uploader_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_uploader_active_task
ON uploader_collection_tasks(uploader_id) WHERE status='RUNNING';
CREATE TABLE IF NOT EXISTS uploader_videos (
    uploader_id INTEGER NOT NULL,
    bvid TEXT NOT NULL,
    title TEXT NOT NULL,
    published_at TEXT NOT NULL,
    duration INTEGER NOT NULL DEFAULT 0,
    partition TEXT NOT NULL DEFAULT '',
    views INTEGER NOT NULL DEFAULT 0,
    likes INTEGER NOT NULL DEFAULT 0,
    coins INTEGER NOT NULL DEFAULT 0,
    favorites INTEGER NOT NULL DEFAULT 0,
    comments INTEGER NOT NULL DEFAULT 0,
    danmaku INTEGER NOT NULL DEFAULT 0,
    shares INTEGER NOT NULL DEFAULT 0,
    visibility_status TEXT NOT NULL DEFAULT 'VISIBLE',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (uploader_id,bvid),
    FOREIGN KEY (uploader_id) REFERENCES uploader_profiles(uploader_id)
);
CREATE TABLE IF NOT EXISTS uploader_video_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uploader_id INTEGER NOT NULL,
    bvid TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    views INTEGER NOT NULL DEFAULT 0,
    likes INTEGER NOT NULL DEFAULT 0,
    coins INTEGER NOT NULL DEFAULT 0,
    favorites INTEGER NOT NULL DEFAULT 0,
    comments INTEGER NOT NULL DEFAULT 0,
    danmaku INTEGER NOT NULL DEFAULT 0,
    shares INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (uploader_id,bvid) REFERENCES uploader_videos(uploader_id,bvid),
    UNIQUE (uploader_id,bvid,collected_at)
);
"""


def initialize_uploader_database(database_path=DATABASE_PATH):
    with connect_database(database_path) as connection:
        connection.executescript(UPLOADER_TABLES_SQL)


def sync_ranked_uploaders(database_path=DATABASE_PATH):
    initialize_uploader_database(database_path)
    with connect_database(database_path) as connection:
        rows = connection.execute(
            """SELECT uploader_id,MIN(collected_at) first_ranked_at,
            MAX(collected_at) last_ranked_at FROM ranking_items
            WHERE uploader_id IS NOT NULL GROUP BY uploader_id"""
        ).fetchall()
        now_text = datetime_to_text(datetime.now(UTC))
        for row in rows:
            latest = connection.execute(
                """SELECT uploader FROM ranking_items WHERE uploader_id=?
                ORDER BY collected_at DESC,id DESC LIMIT 1""",
                (row["uploader_id"],),
            ).fetchone()
            connection.execute(
                """INSERT INTO uploader_profiles (
                    uploader_id,current_name,first_ranked_at,last_ranked_at,updated_at
                ) VALUES (?,?,?,?,?) ON CONFLICT(uploader_id) DO UPDATE SET
                    current_name=excluded.current_name,
                    first_ranked_at=MIN(first_ranked_at,excluded.first_ranked_at),
                    last_ranked_at=MAX(last_ranked_at,excluded.last_ranked_at),
                    updated_at=excluded.updated_at""",
                (
                    row["uploader_id"],
                    latest["uploader"],
                    row["first_ranked_at"],
                    row["last_ranked_at"],
                    now_text,
                ),
            )
        return len(rows)


def list_ranked_uploaders(database_path=DATABASE_PATH):
    initialize_uploader_database(database_path)
    with connect_database(database_path) as connection:
        rows = connection.execute(
            """SELECT p.*,COUNT(DISTINCT r.bvid) ranked_video_count,
            (SELECT status FROM uploader_collection_tasks t
             WHERE t.uploader_id=p.uploader_id ORDER BY t.id DESC LIMIT 1)
             collection_status
            FROM uploader_profiles p LEFT JOIN ranking_items r
            ON r.uploader_id=p.uploader_id GROUP BY p.uploader_id
            ORDER BY p.last_ranked_at DESC"""
        ).fetchall()
        return [dict(row) for row in rows]


def create_collection_task(uploader_id, database_path=DATABASE_PATH, now=None):
    initialize_uploader_database(database_path)
    now_text = datetime_to_text(now or datetime.now(UTC))
    with connect_database(database_path) as connection:
        active = connection.execute(
            """SELECT id FROM uploader_collection_tasks
            WHERE uploader_id=? AND status='RUNNING'""",
            (uploader_id,),
        ).fetchone()
        if active is not None:
            return active["id"]
        try:
            cursor = connection.execute(
                """INSERT INTO uploader_collection_tasks (
                    uploader_id,status,cursor,started_at,updated_at
                ) VALUES (?,'RUNNING',1,?,?)""",
                (uploader_id, now_text, now_text),
            )
        except sqlite3.IntegrityError as error:
            raise ValueError("UP 身份未确认，不能创建采集任务") from error
        return cursor.lastrowid


def _video_values(raw):
    stat = raw.get("stat") or {}
    return {
        "bvid": raw["bvid"],
        "title": raw.get("title") or raw["bvid"],
        "published_at": datetime.fromtimestamp(
            raw.get("pubdate", raw.get("created", 0)), tz=UTC
        ),
        "duration": int(raw.get("duration") or 0),
        "partition": raw.get("tname") or raw.get("partition") or "",
        "views": int(stat.get("view", raw.get("play", 0)) or 0),
        "likes": int(stat.get("like", raw.get("likes", 0)) or 0),
        "coins": int(stat.get("coin", raw.get("coins", 0)) or 0),
        "favorites": int(stat.get("favorite", raw.get("favorites", 0)) or 0),
        "comments": int(stat.get("reply", raw.get("comments", 0)) or 0),
        "danmaku": int(stat.get("danmaku", raw.get("danmaku", 0)) or 0),
        "shares": int(stat.get("share", raw.get("shares", 0)) or 0),
    }


def save_uploader_page(
    task_id,
    uploader_id,
    videos,
    next_cursor,
    has_more,
    collected_at,
    database_path=DATABASE_PATH,
):
    collected_text = datetime_to_text(collected_at)
    with connect_database(database_path) as connection:
        task = connection.execute(
            """SELECT id FROM uploader_collection_tasks
            WHERE id=? AND uploader_id=? AND status='RUNNING'""",
            (task_id, uploader_id),
        ).fetchone()
        if task is None:
            raise ValueError("UP 采集任务不存在或已经结束")
        for raw in videos:
            video = _video_values(raw)
            values = (
                uploader_id,
                video["bvid"],
                video["title"],
                datetime_to_text(video["published_at"]),
                video["duration"],
                video["partition"],
                video["views"],
                video["likes"],
                video["coins"],
                video["favorites"],
                video["comments"],
                video["danmaku"],
                video["shares"],
                collected_text,
            )
            connection.execute(
                """INSERT INTO uploader_videos (
                    uploader_id,bvid,title,published_at,duration,partition,
                    views,likes,coins,favorites,comments,danmaku,shares,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(uploader_id,bvid) DO UPDATE SET
                    title=excluded.title,views=excluded.views,likes=excluded.likes,
                    coins=excluded.coins,favorites=excluded.favorites,
                    comments=excluded.comments,danmaku=excluded.danmaku,
                    shares=excluded.shares,updated_at=excluded.updated_at""",
                values,
            )
            connection.execute(
                """INSERT OR IGNORE INTO uploader_video_snapshots (
                    uploader_id,bvid,collected_at,views,likes,coins,
                    favorites,comments,danmaku,shares
                ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    uploader_id,
                    video["bvid"],
                    collected_text,
                    video["views"],
                    video["likes"],
                    video["coins"],
                    video["favorites"],
                    video["comments"],
                    video["danmaku"],
                    video["shares"],
                ),
            )
        status = "RUNNING" if has_more else "SUCCEEDED"
        connection.execute(
            """UPDATE uploader_collection_tasks SET cursor=?,status=?,updated_at=?,
            finished_at=CASE WHEN ?='SUCCEEDED' THEN ? ELSE NULL END WHERE id=?""",
            (next_cursor, status, collected_text, status, collected_text, task_id),
        )


def fail_collection_task(task_id, error_code, database_path=DATABASE_PATH):
    now_text = datetime_to_text(datetime.now(UTC))
    with connect_database(database_path) as connection:
        connection.execute(
            """UPDATE uploader_collection_tasks SET status='FAILED',error_code=?,
            updated_at=?,finished_at=? WHERE id=? AND status='RUNNING'""",
            (error_code, now_text, now_text, task_id),
        )


def get_uploader_detail(uploader_id, database_path=DATABASE_PATH):
    initialize_uploader_database(database_path)
    with connect_database(database_path) as connection:
        profile = connection.execute(
            "SELECT * FROM uploader_profiles WHERE uploader_id=?", (uploader_id,)
        ).fetchone()
        if profile is None:
            return None
        task = connection.execute(
            """SELECT * FROM uploader_collection_tasks WHERE uploader_id=?
            ORDER BY id DESC LIMIT 1""",
            (uploader_id,),
        ).fetchone()
        videos = connection.execute(
            """SELECT * FROM uploader_videos WHERE uploader_id=?
            ORDER BY published_at DESC""",
            (uploader_id,),
        ).fetchall()
        rankings = connection.execute(
            """SELECT bvid,rank,partition,collected_at FROM ranking_items
            WHERE uploader_id=? ORDER BY collected_at,rank""",
            (uploader_id,),
        ).fetchall()
        return {
            "profile": dict(profile),
            "task": dict(task) if task else None,
            "videos": [dict(row) for row in videos],
            "ranked_bvids": {row["bvid"] for row in rankings},
            "rankings": [dict(row) for row in rankings],
        }


__all__ = [
    "create_collection_task",
    "fail_collection_task",
    "get_uploader_detail",
    "initialize_uploader_database",
    "list_ranked_uploaders",
    "save_uploader_page",
    "sync_ranked_uploaders",
]
