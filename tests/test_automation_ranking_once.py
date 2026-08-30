import json
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from automation.ranking_once import (
    acquire_process_lock,
    exit_code_for_status,
    release_process_lock,
    run_ranking_once,
)
from automation.reports import format_json_report, safe_error_message
from ranking_collector.repository import initialize_database

PARTITIONS = ("全站", "知识", "科技", "游戏", "生活")


def _insert_run(database_path, succeeded, successful_partitions=PARTITIONS):
    now = datetime(2026, 8, 30, tzinfo=UTC).isoformat()
    with sqlite3.connect(database_path) as connection:
        cursor = connection.execute(
            "INSERT INTO collection_runs "
            "(started_at,finished_at,succeeded,error_message) VALUES (?,?,?,?)",
            (now, now, int(succeeded), None if succeeded else "部分分区失败"),
        )
        run_id = cursor.lastrowid
        for partition in PARTITIONS:
            partition_succeeded = partition in successful_partitions
            connection.execute(
                "INSERT INTO collection_partition_results "
                "(run_id,partition,collected_at,succeeded,error_message) "
                "VALUES (?,?,?,?,?)",
                (
                    run_id,
                    partition,
                    now,
                    int(partition_succeeded),
                    None if partition_succeeded else "接口失败",
                ),
            )
            if partition_succeeded:
                snapshot = connection.execute(
                    "INSERT INTO ranking_snapshots "
                    "(run_id,partition,collected_at) VALUES (?,?,?)",
                    (run_id, partition, now),
                ).lastrowid
                connection.execute(
                    "INSERT INTO ranking_items "
                    "(snapshot_id,bvid,title,uploader,uploader_id,partition,"
                    "published_at,rank,views,likes,coins,favorites,comments,"
                    "danmaku,shares,collected_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        snapshot,
                        f"BV{run_id}{partition}",
                        "测试视频",
                        "测试 UP",
                        123,
                        partition,
                        now,
                        1,
                        100,
                        10,
                        2,
                        3,
                        4,
                        5,
                        6,
                        now,
                    ),
                )
    return run_id


def test_success_requires_completed_run_partition_results_and_items(tmp_path):
    database_path = tmp_path / "ranking.db"
    initialize_database(database_path)

    def launch_worker(_command, _timeout):
        _insert_run(database_path, True)
        return SimpleNamespace(returncode=0)

    result = run_ranking_once(
        database_path=database_path,
        timeout=30,
        launch_worker=launch_worker,
    )
    assert result["status"] == "SUCCEEDED"
    assert result["run_id"] == 1
    assert [item["partition"] for item in result["partitions"]] == list(
        PARTITIONS
    )
    assert all(item["item_count"] == 1 for item in result["partitions"])


def test_partial_failure_is_reported_from_database_not_exit_code(tmp_path):
    database_path = tmp_path / "ranking.db"
    initialize_database(database_path)

    def launch_worker(_command, _timeout):
        _insert_run(database_path, False, ("全站", "知识"))
        return SimpleNamespace(returncode=0)

    result = run_ranking_once(
        database_path=database_path,
        timeout=30,
        launch_worker=launch_worker,
    )
    assert result["status"] == "PARTIAL_FAILED"
    assert result["partitions"][0]["succeeded"] is True
    assert result["partitions"][2]["error"] == "接口失败"


def test_successful_partition_without_saved_items_fails_validation(tmp_path):
    database_path = tmp_path / "ranking.db"
    initialize_database(database_path)

    def launch_worker(_command, _timeout):
        _insert_run(database_path, True)
        with sqlite3.connect(database_path) as connection:
            connection.execute("DELETE FROM ranking_items")
        return SimpleNamespace(returncode=0)

    result = run_ranking_once(
        database_path=database_path,
        launch_worker=launch_worker,
    )
    assert result["status"] == "FAILED"
    assert all(item["succeeded"] is False for item in result["partitions"])
    assert result["partitions"][0]["error"] == "数据库未保存榜单条目"


def test_missing_partition_results_are_reported_as_partial_failure(tmp_path):
    database_path = tmp_path / "ranking.db"
    initialize_database(database_path)

    def launch_worker(_command, _timeout):
        run_id = _insert_run(database_path, True)
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                "DELETE FROM collection_partition_results "
                "WHERE run_id=? AND partition<>'全站'",
                (run_id,),
            )
            connection.execute(
                "DELETE FROM ranking_items WHERE partition<>'全站'"
            )
        return SimpleNamespace(returncode=0)

    result = run_ranking_once(
        database_path=database_path,
        launch_worker=launch_worker,
    )
    assert result["status"] == "PARTIAL_FAILED"
    assert len(result["partitions"]) == 5
    assert result["partitions"][1]["error"] == "分区采集结果缺失"


def test_active_database_run_skips_without_launching_worker(tmp_path):
    database_path = tmp_path / "ranking.db"
    initialize_database(database_path)
    now = datetime(2026, 8, 30, tzinfo=UTC)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO collection_runs (started_at) VALUES (?)",
            (now.isoformat(),),
        )
    launches = []
    result = run_ranking_once(
        database_path=database_path,
        timeout=30,
        now=lambda: now + timedelta(minutes=1),
        launch_worker=lambda *_args: launches.append(True),
    )
    assert result["status"] == "SKIPPED_ALREADY_RUNNING"
    assert launches == []


def test_timed_out_worker_marks_its_unfinished_run_failed(tmp_path):
    database_path = tmp_path / "ranking.db"
    initialize_database(database_path)

    def launch_worker(command, timeout):
        _ = command
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                "INSERT INTO collection_runs (started_at) VALUES (?)",
                (datetime(2026, 8, 30, tzinfo=UTC).isoformat(),),
            )
        raise subprocess.TimeoutExpired("worker", timeout)

    result = run_ranking_once(
        database_path=database_path,
        timeout=3,
        launch_worker=launch_worker,
    )
    assert result["status"] == "TIMED_OUT"
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT succeeded,error_message FROM collection_runs WHERE id=1"
        ).fetchone()
    assert row == (0, "采集任务超时")


def test_stale_unfinished_run_is_closed_before_new_collection(tmp_path):
    database_path = tmp_path / "ranking.db"
    initialize_database(database_path)
    now = datetime(2026, 8, 30, tzinfo=UTC)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO collection_runs (started_at) VALUES (?)",
            ((now - timedelta(hours=2)).isoformat(),),
        )

    def launch_worker(_command, _timeout):
        _insert_run(database_path, True)
        return SimpleNamespace(returncode=0)

    result = run_ranking_once(
        database_path=database_path,
        timeout=30,
        stale_after=timedelta(minutes=30),
        now=lambda: now,
        launch_worker=launch_worker,
    )
    assert result["status"] == "SUCCEEDED"
    assert result["run_id"] == 2
    with sqlite3.connect(database_path) as connection:
        stale = connection.execute(
            "SELECT succeeded,error_message FROM collection_runs WHERE id=1"
        ).fetchone()
    assert stale == (0, "遗留采集任务已标记为中断")


def test_process_lock_rejects_second_owner(tmp_path):
    lock_path = tmp_path / "ranking-once.lock"
    first = acquire_process_lock(lock_path)
    try:
        assert first is not None
        assert acquire_process_lock(lock_path) is None
    finally:
        release_process_lock(first)


def test_status_exit_codes_are_stable():
    assert exit_code_for_status("SUCCEEDED") == 0
    assert exit_code_for_status("PARTIAL_FAILED") == 2
    assert exit_code_for_status("SKIPPED_ALREADY_RUNNING") == 3
    assert exit_code_for_status("TIMED_OUT") == 4
    assert exit_code_for_status("FAILED") == 1


def test_report_is_json_and_redacts_sensitive_error_text():
    message = "Cookie failed at E:\\summary_video\\.secrets\\cookie.txt api_key=abc"
    assert safe_error_message(message) == "采集请求失败，请检查网络或本地凭据"
    report = format_json_report(
        {"status": "FAILED", "run_id": None, "partitions": []}
    )
    assert json.loads(report)["status"] == "FAILED"
    assert "Cookie" not in report


def test_worker_start_failure_returns_safe_failed_result(tmp_path):
    database_path = tmp_path / "ranking.db"

    def launch_worker(_command, _timeout):
        raise OSError("cannot start E:\\summary_video\\secret-worker.exe")

    result = run_ranking_once(
        database_path=database_path,
        launch_worker=launch_worker,
    )
    assert result["status"] == "FAILED"
    assert result["run_id"] is None
    assert result["message"] == "无法启动排行榜采集进程"


def test_json_cli_skips_active_run_without_network(tmp_path):
    database_path = tmp_path / "ranking.db"
    initialize_database(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO collection_runs (started_at) VALUES (?)",
            (datetime.now(UTC).isoformat(),),
        )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "automation.ranking_once",
            "--database",
            str(database_path),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 3
    assert json.loads(completed.stdout)["status"] == "SKIPPED_ALREADY_RUNNING"
    assert completed.stderr == ""
