import sqlite3

import pytest

from web_app.summary.repository import (
    RepositoryError,
    create_summary_task,
    find_active_task,
    find_latest_success,
    get_summary_task,
    initialize_summary_tables,
    list_summary_tasks,
    recover_incomplete_tasks,
    transition_task,
)

VALID_URL = "https://www.bilibili.com/video/BV1TEST"


def test_initialization_is_idempotent_and_creates_task_tables(tmp_path):
    database_path = tmp_path / "summary.db"

    initialize_summary_tables(database_path)
    initialize_summary_tables(database_path)

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
    assert {"summary_tasks", "summary_attempts"} <= tables
    assert "idx_summary_tasks_one_active_video" in indexes


def test_create_get_and_list_tasks_newest_first(tmp_path):
    database_path = tmp_path / "summary.db"
    initialize_summary_tables(database_path)

    first = create_summary_task("BV1FIRST", VALID_URL, database_path)
    second = create_summary_task(
        "BV1SECOND",
        "https://www.bilibili.com/video/BV1SECOND",
        database_path,
    )

    loaded = get_summary_task(first.task_id, database_path)
    tasks = list_summary_tasks(2, database_path)

    assert loaded is not None
    assert loaded.task_id == first.task_id
    assert loaded.video_id == "BV1FIRST"
    assert loaded.status == "PENDING"
    assert loaded.stage is None
    assert loaded.result is None
    assert loaded.error is None
    assert [task.task_id for task in tasks] == [second.task_id, first.task_id]
    missing = get_summary_task(
        "d2e8ce64-1de3-45ae-a344-67bcb064d26e",
        database_path,
    )
    assert missing is None


def test_only_one_active_task_per_video(tmp_path):
    database_path = tmp_path / "summary.db"
    initialize_summary_tables(database_path)
    first = create_summary_task("BV1TEST", VALID_URL, database_path)

    active = find_active_task("BV1TEST", database_path)

    assert active is not None
    assert active.task_id == first.task_id
    with pytest.raises(RepositoryError, match="active"):
        create_summary_task("BV1TEST", VALID_URL, database_path)


def test_transition_requires_expected_status_and_legal_state_change(tmp_path):
    database_path = tmp_path / "summary.db"
    initialize_summary_tables(database_path)
    task = create_summary_task("BV1TEST", VALID_URL, database_path)

    processing = transition_task(
        task.task_id,
        {"PENDING"},
        "PROCESSING",
        "METADATA",
        None,
        None,
        database_path,
    )

    assert processing.status == "PROCESSING"
    assert processing.stage == "METADATA"
    assert processing.started_at is not None
    with pytest.raises(RepositoryError, match="expected"):
        transition_task(
            task.task_id,
            {"PENDING"},
            "PROCESSING",
            "METADATA",
            None,
            None,
            database_path,
        )
    with pytest.raises(ValueError, match="legal"):
        transition_task(
            task.task_id,
            {"PROCESSING"},
            "PENDING",
            None,
            None,
            None,
            database_path,
        )


def test_success_result_json_roundtrips_and_is_findable(tmp_path):
    database_path = tmp_path / "summary.db"
    initialize_summary_tables(database_path)
    task = create_summary_task("BV1TEST", VALID_URL, database_path)
    transition_task(
        task.task_id,
        {"PENDING"},
        "PROCESSING",
        "SUMMARIZE_CHUNKS",
        None,
        None,
        database_path,
    )
    result = {"overview": {"keywords": ["SQLite", "summary"]}}

    succeeded = transition_task(
        task.task_id,
        {"PROCESSING"},
        "SUCCEEDED",
        "COMPLETE",
        result,
        None,
        database_path,
    )

    latest = find_latest_success("BV1TEST", database_path)
    assert succeeded.result == result
    assert succeeded.finished_at is not None
    assert latest is not None
    assert latest.task_id == task.task_id
    with pytest.raises(ValueError, match="result"):
        transition_task(
            task.task_id,
            {"SUCCEEDED"},
            "SUCCEEDED",
            "COMPLETE",
            None,
            None,
            database_path,
        )


def test_processing_task_is_requeued_on_recovery(tmp_path):
    database_path = tmp_path / "summary.db"
    initialize_summary_tables(database_path)
    task = create_summary_task("BV1TEST", VALID_URL, database_path)
    transition_task(
        task.task_id,
        {"PENDING"},
        "PROCESSING",
        "METADATA",
        None,
        None,
        database_path,
    )

    recovered = recover_incomplete_tasks(database_path)

    assert [item.task_id for item in recovered] == [task.task_id]
    requeued = get_summary_task(task.task_id, database_path)
    assert requeued is not None
    assert requeued.status == "PENDING"
    assert requeued.stage is None
    assert requeued.started_at is None


def test_retry_links_failed_task_and_preserves_attempt_history(tmp_path):
    database_path = tmp_path / "summary.db"
    initialize_summary_tables(database_path)
    failed = create_summary_task("BV1TEST", VALID_URL, database_path)
    transition_task(
        failed.task_id,
        {"PENDING"},
        "PROCESSING",
        "TRANSCRIPT",
        None,
        None,
        database_path,
    )
    transition_task(
        failed.task_id,
        {"PROCESSING"},
        "FAILED",
        "FAILED",
        None,
        "No usable Chinese subtitles",
        database_path,
    )

    retry = create_summary_task(
        "BV1TEST",
        VALID_URL,
        database_path,
        retry_of=failed.task_id,
    )

    assert retry.retry_of == failed.task_id
    assert get_summary_task(failed.task_id, database_path).status == "FAILED"
    with sqlite3.connect(database_path) as connection:
        attempts = connection.execute(
            """
            SELECT task_id, status
            FROM summary_attempts
            ORDER BY created_at ASC, id ASC
            """
        ).fetchall()
    assert attempts == [
        (failed.task_id, "FAILED"),
        (retry.task_id, "PENDING"),
    ]


def test_invalid_task_data_and_transition_invariants_fail_clearly(tmp_path):
    database_path = tmp_path / "summary.db"
    initialize_summary_tables(database_path)

    with pytest.raises(ValueError, match="video_id"):
        create_summary_task("not-a-bvid", VALID_URL, database_path)
    with pytest.raises(ValueError, match="video_url"):
        create_summary_task("BV1TEST", "file:///private/video", database_path)
    with pytest.raises(ValueError, match="limit"):
        list_summary_tasks(0, database_path)

    task = create_summary_task("BV1TEST", VALID_URL, database_path)
    with pytest.raises(ValueError, match="error"):
        transition_task(
            task.task_id,
            {"PENDING"},
            "FAILED",
            "FAILED",
            None,
            None,
            database_path,
        )


def test_processing_rejects_terminal_and_noncanonical_stages(tmp_path):
    database_path = tmp_path / "summary.db"
    initialize_summary_tables(database_path)
    complete_task = create_summary_task("BV1COMPLETE", VALID_URL, database_path)
    failed_task = create_summary_task("BV1FAILED", VALID_URL, database_path)
    summarizing_task = create_summary_task("BV1SUMMARIZING", VALID_URL, database_path)
    merging_task = create_summary_task("BV1MERGING", VALID_URL, database_path)

    with pytest.raises(ValueError, match="PROCESSING stage"):
        transition_task(
            complete_task.task_id,
            {"PENDING"},
            "PROCESSING",
            "COMPLETE",
            None,
            None,
            database_path,
        )
    with pytest.raises(ValueError, match="PROCESSING stage"):
        transition_task(
            failed_task.task_id,
            {"PENDING"},
            "PROCESSING",
            "FAILED",
            None,
            None,
            database_path,
        )
    with pytest.raises(ValueError, match="stage"):
        transition_task(
            summarizing_task.task_id,
            {"PENDING"},
            "PROCESSING",
            "SUMMARIZING",
            None,
            None,
            database_path,
        )
    with pytest.raises(ValueError, match="stage"):
        transition_task(
            merging_task.task_id,
            {"PENDING"},
            "PROCESSING",
            "MERGING",
            None,
            None,
            database_path,
        )
