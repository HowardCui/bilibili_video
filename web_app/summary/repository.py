"""SQLite persistence for recoverable video-summary tasks."""

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

TASK_STATUSES = frozenset({"PENDING", "PROCESSING", "SUCCEEDED", "FAILED"})
PROCESSING_STAGES = frozenset(
    {
        "METADATA",
        "SUBTITLE",
        "TRANSCRIPT",
        "SPLIT",
        "SUMMARIZE_CHUNKS",
        "MERGE",
    }
)
TASK_STAGES = PROCESSING_STAGES | frozenset({"COMPLETE", "FAILED"})
ACTIVE_STATUSES = frozenset({"PENDING", "PROCESSING"})

CREATE_SUMMARY_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS summary_tasks (
    task_id TEXT PRIMARY KEY,
    video_id TEXT NOT NULL,
    video_url TEXT NOT NULL,
    status TEXT NOT NULL,
    stage TEXT,
    result_json TEXT,
    error TEXT,
    retry_of TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    updated_at TEXT NOT NULL,
    finished_at TEXT,
    FOREIGN KEY (retry_of) REFERENCES summary_tasks(task_id),
    CHECK (status IN ('PENDING', 'PROCESSING', 'SUCCEEDED', 'FAILED'))
);

CREATE TABLE IF NOT EXISTS summary_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
    status TEXT NOT NULL,
    stage TEXT,
    result_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    updated_at TEXT NOT NULL,
    finished_at TEXT,
    FOREIGN KEY (task_id) REFERENCES summary_tasks(task_id) ON DELETE CASCADE,
    UNIQUE (task_id, attempt_number),
    CHECK (status IN ('PENDING', 'PROCESSING', 'SUCCEEDED', 'FAILED'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_summary_tasks_one_active_video
    ON summary_tasks(video_id)
    WHERE status IN ('PENDING', 'PROCESSING');

CREATE INDEX IF NOT EXISTS idx_summary_tasks_video_success
    ON summary_tasks(video_id, created_at DESC, task_id DESC)
    WHERE status = 'SUCCEEDED';

CREATE INDEX IF NOT EXISTS idx_summary_attempts_task
    ON summary_attempts(task_id, attempt_number ASC);
"""


class RepositoryError(RuntimeError):
    """Raised when a repository operation cannot satisfy its contract."""


class SummaryTaskRecord:
    """A fully materialized summary task without ORM or dataclass machinery."""

    def __init__(
        self,
        task_id,
        video_id,
        video_url,
        status,
        stage,
        result,
        error,
        retry_of,
        created_at,
        started_at,
        updated_at,
        finished_at,
        attempt_number,
    ):
        self.task_id = task_id
        self.video_id = video_id
        self.video_url = video_url
        self.status = status
        self.stage = stage
        self.result = result
        self.error = error
        self.retry_of = retry_of
        self.created_at = created_at
        self.started_at = started_at
        self.updated_at = updated_at
        self.finished_at = finished_at
        self.attempt_number = attempt_number


def _connect(database_path):
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def _now_text():
    return datetime.now(UTC).isoformat()


def _timestamp_from_text(value):
    if value is None:
        return None
    try:
        timestamp = datetime.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise RepositoryError("database contains an invalid task timestamp") from error
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise RepositoryError("database contains a timezone-naive task timestamp")
    return timestamp.astimezone(UTC)


def _validate_task_id(task_id, field_name="task_id"):
    if not isinstance(task_id, str):
        raise TypeError(f"{field_name} must be a UUID string")
    try:
        parsed = uuid.UUID(task_id)
    except (ValueError, AttributeError) as error:
        raise ValueError(f"{field_name} must be a UUID string") from error
    if str(parsed) != task_id:
        raise ValueError(f"{field_name} must be a canonical UUID string")
    return task_id


def _validate_video_id(video_id):
    if not isinstance(video_id, str):
        raise TypeError("video_id must be a string")
    if not video_id.startswith("BV") or len(video_id) < 5:
        raise ValueError("video_id must be a canonical BV identifier")
    if not video_id.isalnum():
        raise ValueError("video_id must be a canonical BV identifier")
    return video_id


def _validate_video_url(video_url):
    if not isinstance(video_url, str):
        raise TypeError("video_url must be a string")
    parsed = urlsplit(video_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("video_url must be an absolute HTTP(S) URL")
    return video_url


def _validate_status(status, field_name="status"):
    if not isinstance(status, str):
        raise TypeError(f"{field_name} must be a string")
    if status not in TASK_STATUSES:
        raise ValueError(f"{field_name} must be one of {sorted(TASK_STATUSES)}")
    return status


def _validate_stage(stage):
    if stage is None:
        return None
    if not isinstance(stage, str):
        raise TypeError("stage must be a string or None")
    if stage not in TASK_STAGES:
        raise ValueError(f"stage must be one of {sorted(TASK_STAGES)}")
    return stage


def _validate_result(result):
    if result is None:
        return None
    if not isinstance(result, dict):
        raise TypeError("result must be a dictionary or None")
    try:
        return json.dumps(result, allow_nan=False, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as error:
        raise ValueError("result must be JSON serializable") from error


def _validate_error(error):
    if error is None:
        return None
    if not isinstance(error, str):
        raise TypeError("error must be a string or None")
    if not error.strip():
        raise ValueError("error must not be blank")
    return error


def _validate_transition_values(new_status, stage, result, error):
    _validate_status(new_status, "new_status")
    _validate_stage(stage)
    result_json = _validate_result(result)
    _validate_error(error)

    if new_status == "PENDING":
        if stage is not None or result is not None or error is not None:
            raise ValueError("PENDING tasks cannot have stage, result, or error")
    elif new_status == "PROCESSING":
        if stage is None or result is not None or error is not None:
            raise ValueError("PROCESSING tasks require a stage and no result or error")
        if stage not in PROCESSING_STAGES:
            raise ValueError("PROCESSING stage must be a non-terminal pipeline stage")
    elif new_status == "SUCCEEDED":
        if stage != "COMPLETE" or result is None or error is not None:
            raise ValueError("SUCCEEDED tasks require COMPLETE stage and a result")
    elif new_status == "FAILED":
        if stage != "FAILED" or result is not None or error is None:
            raise ValueError("FAILED tasks require FAILED stage and an error")
    return result_json


def _validate_expected_statuses(expected_statuses):
    if not isinstance(expected_statuses, (set, frozenset)) or not expected_statuses:
        raise TypeError("expected_statuses must be a non-empty set of statuses")
    for status in expected_statuses:
        _validate_status(status, "expected_status")
    return frozenset(expected_statuses)


def _is_legal_transition(current_status, new_status):
    legal_transitions = {
        "PENDING": {"PROCESSING"},
        "PROCESSING": {"PROCESSING", "SUCCEEDED", "FAILED"},
        "SUCCEEDED": set(),
        "FAILED": set(),
    }
    return new_status in legal_transitions[current_status]


def _record_from_row(row):
    try:
        result = json.loads(row["result_json"]) if row["result_json"] else None
    except (TypeError, ValueError) as error:
        raise RepositoryError("database contains an invalid task result") from error
    return SummaryTaskRecord(
        task_id=row["task_id"],
        video_id=row["video_id"],
        video_url=row["video_url"],
        status=row["status"],
        stage=row["stage"],
        result=result,
        error=row["error"],
        retry_of=row["retry_of"],
        created_at=_timestamp_from_text(row["created_at"]),
        started_at=_timestamp_from_text(row["started_at"]),
        updated_at=_timestamp_from_text(row["updated_at"]),
        finished_at=_timestamp_from_text(row["finished_at"]),
        attempt_number=row["attempt_number"],
    )


def _select_task(connection, task_id):
    return connection.execute(
        """
        SELECT summary_tasks.*, summary_attempts.attempt_number
        FROM summary_tasks
        JOIN summary_attempts ON summary_attempts.task_id = summary_tasks.task_id
        WHERE summary_tasks.task_id = ?
        ORDER BY summary_attempts.id DESC
        LIMIT 1
        """,
        (task_id,),
    ).fetchone()


def initialize_summary_tables(database_path) -> None:
    """Create or migrate the standalone summary-task tables."""
    with _connect(database_path) as connection:
        connection.executescript(CREATE_SUMMARY_TABLES_SQL)


def create_summary_task(video_id, video_url, database_path, retry_of=None):
    """Create one pending summary task and its durable execution attempt."""
    _validate_video_id(video_id)
    _validate_video_url(video_url)
    if retry_of is not None:
        _validate_task_id(retry_of, "retry_of")

    task_id = str(uuid.uuid4())
    now = _now_text()
    try:
        with _connect(database_path) as connection:
            attempt_number = 1
            if retry_of is not None:
                prior = connection.execute(
                    "SELECT video_id, status FROM summary_tasks WHERE task_id = ?",
                    (retry_of,),
                ).fetchone()
                if prior is None:
                    raise RepositoryError("retry_of task does not exist")
                if prior["video_id"] != video_id:
                    raise ValueError("retry_of must belong to the same video_id")
                if prior["status"] != "FAILED":
                    raise ValueError("retry_of must reference a FAILED task")
                attempt_number = connection.execute(
                    "SELECT COUNT(*) FROM summary_tasks WHERE video_id = ?",
                    (video_id,),
                ).fetchone()[0] + 1

            connection.execute(
                """
                INSERT INTO summary_tasks (
                    task_id, video_id, video_url, status, stage, result_json,
                    error, retry_of, created_at, started_at, updated_at, finished_at
                ) VALUES (?, ?, ?, 'PENDING', NULL, NULL, NULL, ?, ?, NULL, ?, NULL)
                """,
                (task_id, video_id, video_url, retry_of, now, now),
            )
            connection.execute(
                """
                INSERT INTO summary_attempts (
                    task_id, attempt_number, status, stage, result_json, error,
                    created_at, started_at, updated_at, finished_at
                ) VALUES (?, ?, 'PENDING', NULL, NULL, NULL, ?, NULL, ?, NULL)
                """,
                (task_id, attempt_number, now, now),
            )
            return _record_from_row(_select_task(connection, task_id))
    except sqlite3.IntegrityError as error:
        constraint_failed = (
            "idx_summary_tasks_one_active_video" in str(error)
            or "summary_tasks.video_id" in str(error)
        )
        if constraint_failed:
            raise RepositoryError(
                "an active task already exists for this video"
            ) from error
        raise RepositoryError(f"summary task creation failed: {error}") from error


def get_summary_task(task_id, database_path):
    """Return the exact task identified by a UUID, or None when it is absent."""
    _validate_task_id(task_id)
    with _connect(database_path) as connection:
        row = _select_task(connection, task_id)
    return _record_from_row(row) if row is not None else None


def find_active_task(video_id, database_path):
    """Return the sole pending or processing task for a video, if present."""
    _validate_video_id(video_id)
    with _connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT summary_tasks.*, summary_attempts.attempt_number
            FROM summary_tasks
            JOIN summary_attempts ON summary_attempts.task_id = summary_tasks.task_id
            WHERE summary_tasks.video_id = ?
              AND summary_tasks.status IN ('PENDING', 'PROCESSING')
            ORDER BY summary_tasks.created_at DESC, summary_tasks.rowid DESC
            LIMIT 1
            """,
            (video_id,),
        ).fetchone()
    return _record_from_row(row) if row is not None else None


def find_latest_success(video_id, database_path):
    """Return the newest completed task for a video, if one exists."""
    _validate_video_id(video_id)
    with _connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT summary_tasks.*, summary_attempts.attempt_number
            FROM summary_tasks
            JOIN summary_attempts ON summary_attempts.task_id = summary_tasks.task_id
            WHERE summary_tasks.video_id = ? AND summary_tasks.status = 'SUCCEEDED'
            ORDER BY summary_tasks.created_at DESC, summary_tasks.rowid DESC
            LIMIT 1
            """,
            (video_id,),
        ).fetchone()
    return _record_from_row(row) if row is not None else None


def list_summary_tasks(limit, database_path):
    """List summary tasks deterministically from newest creation to oldest."""
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise TypeError("limit must be an integer")
    if limit < 1:
        raise ValueError("limit must be greater than 0")
    with _connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT summary_tasks.*, summary_attempts.attempt_number
            FROM summary_tasks
            JOIN summary_attempts ON summary_attempts.task_id = summary_tasks.task_id
            ORDER BY summary_tasks.created_at DESC, summary_tasks.rowid DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_record_from_row(row) for row in rows]


def transition_task(
    task_id,
    expected_statuses,
    new_status,
    stage,
    result,
    error,
    database_path,
):
    """Conditionally move a task through its explicit durable state machine."""
    _validate_task_id(task_id)
    expected_statuses = _validate_expected_statuses(expected_statuses)
    result_json = _validate_transition_values(new_status, stage, result, error)
    now = _now_text()

    with _connect(database_path) as connection:
        current = _select_task(connection, task_id)
        if current is None:
            raise RepositoryError("summary task does not exist")
        if current["status"] not in expected_statuses:
            raise RepositoryError("summary task is not in an expected status")
        if not _is_legal_transition(current["status"], new_status):
            raise ValueError("illegal summary task status transition")

        started_at = current["started_at"]
        finished_at = current["finished_at"]
        if new_status == "PROCESSING" and started_at is None:
            started_at = now
        if new_status in {"SUCCEEDED", "FAILED"}:
            finished_at = now

        placeholders = ", ".join("?" for _ in expected_statuses)
        cursor = connection.execute(
            f"""
            UPDATE summary_tasks
            SET status = ?, stage = ?, result_json = ?, error = ?, started_at = ?,
                updated_at = ?, finished_at = ?
            WHERE task_id = ? AND status IN ({placeholders})
            """,
            (
                new_status,
                stage,
                result_json,
                error,
                started_at,
                now,
                finished_at,
                task_id,
                *sorted(expected_statuses),
            ),
        )
        if cursor.rowcount != 1:
            raise RepositoryError("summary task is not in an expected status")
        connection.execute(
            """
            UPDATE summary_attempts
            SET status = ?, stage = ?, result_json = ?, error = ?, started_at = ?,
                updated_at = ?, finished_at = ?
            WHERE task_id = ?
            """,
            (
                new_status,
                stage,
                result_json,
                error,
                started_at,
                now,
                finished_at,
                task_id,
            ),
        )
        return _record_from_row(_select_task(connection, task_id))


def recover_incomplete_tasks(database_path):
    """Requeue pending work and safely return abandoned processing work to pending."""
    now = _now_text()
    with _connect(database_path) as connection:
        processing_ids = [
            row["task_id"]
            for row in connection.execute(
                "SELECT task_id FROM summary_tasks WHERE status = 'PROCESSING'"
            ).fetchall()
        ]
        if processing_ids:
            placeholders = ", ".join("?" for _ in processing_ids)
            connection.execute(
                f"""
                UPDATE summary_tasks
                SET status = 'PENDING', stage = NULL, result_json = NULL, error = NULL,
                    started_at = NULL, updated_at = ?, finished_at = NULL
                WHERE task_id IN ({placeholders}) AND status = 'PROCESSING'
                """,
                (now, *processing_ids),
            )
            connection.execute(
                f"""
                UPDATE summary_attempts
                SET status = 'PENDING', stage = NULL, result_json = NULL, error = NULL,
                    started_at = NULL, updated_at = ?, finished_at = NULL
                WHERE task_id IN ({placeholders}) AND status = 'PROCESSING'
                """,
                (now, *processing_ids),
            )
        rows = connection.execute(
            """
            SELECT summary_tasks.*, summary_attempts.attempt_number
            FROM summary_tasks
            JOIN summary_attempts ON summary_attempts.task_id = summary_tasks.task_id
            WHERE summary_tasks.status = 'PENDING'
            ORDER BY summary_tasks.created_at ASC, summary_tasks.rowid ASC
            """
        ).fetchall()
    return [_record_from_row(row) for row in rows]


__all__ = [
    "RepositoryError",
    "SummaryTaskRecord",
    "create_summary_task",
    "find_active_task",
    "find_latest_success",
    "get_summary_task",
    "initialize_summary_tables",
    "list_summary_tasks",
    "recover_incomplete_tasks",
    "transition_task",
]
