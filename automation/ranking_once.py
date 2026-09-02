"""带并发、超时和数据库验证的排行榜单次采集入口。"""

import argparse
import os
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app_logging import configure_logging, get_logger, log_event
from ranking_collector.config import DATABASE_PATH
from ranking_collector.repository import (
    get_enabled_partition_names,
    initialize_database,
)

from .reports import format_json_report, format_text_report, safe_error_message

DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_STALE_MINUTES = 30
LOGGER = get_logger("automation.ranking_once")


def acquire_process_lock(lock_path):
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock_file = path.open("a+b")
        lock_file.seek(0)
        if lock_file.read(1) == b"":
            lock_file.write(b"0")
            lock_file.flush()
        lock_file.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, PermissionError):
        if "lock_file" in locals():
            lock_file.close()
        return None
    return lock_file


def release_process_lock(lock_file):
    if lock_file is None or lock_file.closed:
        return
    try:
        lock_file.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finally:
        lock_file.close()


def _connect(database_path):
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    return connection


def _latest_run_id(database_path):
    with _connect(database_path) as connection:
        row = connection.execute("SELECT MAX(id) id FROM collection_runs").fetchone()
    return int(row["id"] or 0)


def _unfinished_runs(database_path):
    with _connect(database_path) as connection:
        return connection.execute(
            "SELECT id,started_at FROM collection_runs "
            "WHERE succeeded IS NULL ORDER BY id"
        ).fetchall()


def _close_run(database_path, run_id, message, finished_at):
    with _connect(database_path) as connection:
        connection.execute(
            "UPDATE collection_runs SET finished_at=?,succeeded=0,error_message=? "
            "WHERE id=? AND succeeded IS NULL",
            (finished_at.isoformat(), message, run_id),
        )


def _prepare_unfinished_runs(database_path, current_time, stale_after):
    active = []
    for row in _unfinished_runs(database_path):
        started_at = datetime.fromisoformat(row["started_at"])
        if current_time - started_at <= stale_after:
            active.append(int(row["id"]))
        else:
            _close_run(
                database_path,
                int(row["id"]),
                "遗留采集任务已标记为中断",
                current_time,
            )
    return active


def _default_launch_worker(command, timeout):
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=environment,
    )


def _partition_results(database_path, run_id):
    with _connect(database_path) as connection:
        rows = connection.execute(
            """SELECT result.partition,result.collected_at,result.succeeded,
            result.error_message,
            COALESCE((SELECT COUNT(*) FROM ranking_items item
                JOIN ranking_snapshots snapshot ON snapshot.id=item.snapshot_id
                WHERE snapshot.run_id=result.run_id
                  AND snapshot.partition=result.partition),0) item_count
            FROM collection_partition_results result
            WHERE result.run_id=? ORDER BY result.id""",
            (run_id,),
        ).fetchall()
    rows_by_partition = {row["partition"]: row for row in rows}
    results = []
    for partition in get_enabled_partition_names():
        row = rows_by_partition.get(partition)
        if row is None:
            results.append(
                {
                    "partition": partition,
                    "collected_at": None,
                    "succeeded": False,
                    "item_count": 0,
                    "error": "分区采集结果缺失",
                }
            )
            continue
        item_count = int(row["item_count"])
        succeeded = bool(row["succeeded"]) and item_count > 0
        error = safe_error_message(row["error_message"])
        if row["succeeded"] and item_count == 0:
            error = "数据库未保存榜单条目"
        results.append(
            {
                "partition": row["partition"],
                "collected_at": row["collected_at"],
                "succeeded": succeeded,
                "item_count": item_count,
                "error": error,
            }
        )
    return results


def _read_run(database_path, run_id):
    with _connect(database_path) as connection:
        row = connection.execute(
            "SELECT * FROM collection_runs WHERE id=?", (run_id,)
        ).fetchone()
    return row


def _result_from_database(database_path, run_id, process_returncode=None):
    row = _read_run(database_path, run_id)
    if row is None:
        return {
            "status": "FAILED",
            "run_id": None,
            "started_at": None,
            "finished_at": None,
            "duration_seconds": None,
            "partitions": [],
            "message": "采集进程未创建数据库任务",
            "process_returncode": process_returncode,
        }
    partitions = _partition_results(database_path, run_id)
    successful = [item for item in partitions if item["succeeded"]]
    if row["succeeded"] == 1 and len(successful) == len(partitions) and partitions:
        status = "SUCCEEDED"
    elif successful:
        status = "PARTIAL_FAILED"
    else:
        status = "FAILED"
    started_at = datetime.fromisoformat(row["started_at"])
    finished_at = (
        datetime.fromisoformat(row["finished_at"])
        if row["finished_at"] is not None
        else None
    )
    duration = (
        max(0.0, (finished_at - started_at).total_seconds())
        if finished_at is not None
        else None
    )
    return {
        "status": status,
        "run_id": run_id,
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "duration_seconds": duration,
        "partitions": partitions,
        "message": safe_error_message(row["error_message"]),
        "process_returncode": process_returncode,
    }


def run_ranking_once(
    database_path=DATABASE_PATH,
    timeout=DEFAULT_TIMEOUT_SECONDS,
    stale_after=timedelta(minutes=DEFAULT_STALE_MINUTES),
    now=None,
    launch_worker=None,
):
    database_path = Path(database_path)
    initialize_database(database_path)
    current_time = (now or (lambda: datetime.now(UTC)))()
    lock_file = acquire_process_lock(database_path.with_suffix(".automation.lock"))
    if lock_file is None:
        return {
            "status": "SKIPPED_ALREADY_RUNNING",
            "run_id": None,
            "started_at": None,
            "finished_at": None,
            "duration_seconds": None,
            "partitions": [],
            "message": "已有排行榜单次采集正在运行",
            "process_returncode": None,
        }
    try:
        active_runs = _prepare_unfinished_runs(
            database_path, current_time, stale_after
        )
        if active_runs:
            return {
                "status": "SKIPPED_ALREADY_RUNNING",
                "run_id": active_runs[-1],
                "started_at": None,
                "finished_at": None,
                "duration_seconds": None,
                "partitions": [],
                "message": "数据库中已有未结束的排行榜采集任务",
                "process_returncode": None,
            }
        baseline_run_id = _latest_run_id(database_path)
        command = [
            sys.executable,
            "-m",
            "automation.worker",
            "--database",
            str(database_path),
        ]
        launcher = launch_worker or _default_launch_worker
        try:
            process = launcher(command, timeout)
        except subprocess.TimeoutExpired:
            new_run_id = _latest_run_id(database_path)
            if new_run_id > baseline_run_id:
                _close_run(
                    database_path,
                    new_run_id,
                    "采集任务超时",
                    (now or (lambda: datetime.now(UTC)))(),
                )
            timed_out = _result_from_database(
                database_path,
                new_run_id if new_run_id > baseline_run_id else 0,
                process_returncode=None,
            )
            timed_out["status"] = "TIMED_OUT"
            timed_out["message"] = "采集任务超时"
            return timed_out
        except OSError:
            return {
                "status": "FAILED",
                "run_id": None,
                "started_at": current_time.isoformat(),
                "finished_at": None,
                "duration_seconds": None,
                "partitions": [],
                "message": "无法启动排行榜采集进程",
                "process_returncode": None,
            }
        new_run_id = _latest_run_id(database_path)
        if new_run_id <= baseline_run_id:
            return _result_from_database(
                database_path, 0, process_returncode=process.returncode
            )
        return _result_from_database(
            database_path,
            new_run_id,
            process_returncode=process.returncode,
        )
    finally:
        release_process_lock(lock_file)


def exit_code_for_status(status):
    return {
        "SUCCEEDED": 0,
        "FAILED": 1,
        "PARTIAL_FAILED": 2,
        "SKIPPED_ALREADY_RUNNING": 3,
        "TIMED_OUT": 4,
    }.get(status, 1)


def parse_arguments(arguments=None):
    parser = argparse.ArgumentParser(
        description="执行一次排行榜采集并验证 SQLite 结果"
    )
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--stale-after-minutes", type=int, default=DEFAULT_STALE_MINUTES
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(arguments)


def main(arguments=None):
    options = parse_arguments(arguments)
    configure_logging("automation", console_enabled=not options.json)
    started = time.monotonic()
    log_event(
        LOGGER,
        "INFO",
        "automation_started",
        "排行榜单次采集包装开始",
        task_type="automation",
    )
    result = run_ranking_once(
        database_path=options.database,
        timeout=options.timeout,
        stale_after=timedelta(minutes=options.stale_after_minutes),
    )
    status = result["status"]
    log_event(
        LOGGER,
        "INFO" if status == "SUCCEEDED" else "WARNING",
        "automation_finished",
        f"排行榜单次采集包装结束：{status}",
        task_type="automation",
        run_id=result.get("run_id"),
        duration_ms=round((time.monotonic() - started) * 1000),
    )
    formatter = format_json_report if options.json else format_text_report
    print(formatter(result))
    return exit_code_for_status(result["status"])


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "acquire_process_lock",
    "exit_code_for_status",
    "release_process_lock",
    "run_ranking_once",
]
