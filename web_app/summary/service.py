"""Recoverable background execution for Bilibili summary tasks."""

import logging
import os
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Condition, Event, RLock
from urllib.parse import urlsplit

from app_logging import get_logger, log_event
from web_app.errors import public_error_from_exception, public_summary_result
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

_BVID_PATH = re.compile(r"^/video/(BV[0-9A-Za-z]{3,})/?$", re.IGNORECASE)
_B23_PATH = re.compile(r"^/(BV[0-9A-Za-z]{3,})/?$", re.IGNORECASE)
_BILIBILI_HOSTS = frozenset({"bilibili.com", "www.bilibili.com"})
_WORKER_STORAGE_ATTEMPTS = 3
_WORKER_RECOVERY_RESCHEDULES = 2

_LOGGER = logging.getLogger(__name__)
EVENT_LOGGER = get_logger("summary.service")


class _WorkerStorageError(RuntimeError):
    """Signal an exhausted worker storage boundary without leaking details."""


def _storage_call(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except (sqlite3.Error, OSError) as error:
        raise RuntimeError("summary task storage is temporarily unavailable") from error


class _DatabaseOwnerLock:
    """Hold one cross-process OS lock for a summary database."""

    def __init__(self, database_path):
        database_path = Path(database_path).resolve()
        self._path = database_path.with_name(f"{database_path.name}.summary-owner.lock")
        self._file = None

    def acquire(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = self._path.open("a+b")
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            lock_file.close()
            raise RuntimeError(
                "summary task database is already owned by another service"
            ) from error
        self._file = lock_file

    def release(self):
        lock_file = self._file
        if lock_file is None:
            return
        self._file = None
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


def extract_bvid(video_url) -> str:
    """Extract a canonical BV identifier without following network redirects."""
    if not isinstance(video_url, str):
        raise TypeError("video_url must be a string")
    video_url = video_url.strip()
    if not video_url:
        raise ValueError("video_url must not be blank")

    try:
        parsed = urlsplit(video_url)
        port = parsed.port
    except ValueError as error:
        raise ValueError("video_url must be a valid Bilibili URL") from error
    if parsed.scheme not in {"http", "https"} or parsed.username is not None:
        raise ValueError("video_url must be a valid Bilibili URL")
    if parsed.password is not None or port is not None:
        raise ValueError("video_url must be a valid Bilibili URL")

    host = (parsed.hostname or "").lower().rstrip(".")
    if host in _BILIBILI_HOSTS:
        match = _BVID_PATH.fullmatch(parsed.path)
    elif host == "b23.tv":
        match = _B23_PATH.fullmatch(parsed.path)
    else:
        match = None
    if match is None:
        raise ValueError("video_url must contain an offline-resolvable BV identifier")
    identifier = match.group(1)
    return "BV" + identifier[2:]


class SummaryTaskService:
    """Run summary tasks in-process while keeping their state durable."""

    def __init__(self, database_path, runner, max_workers=2):
        if not callable(runner):
            raise TypeError("runner must be callable")
        if not isinstance(max_workers, int) or isinstance(max_workers, bool):
            raise TypeError("max_workers must be an integer")
        if max_workers < 1:
            raise ValueError("max_workers must be greater than 0")
        self._database_path = database_path
        self._runner = runner
        self._max_workers = max_workers
        self._lock = RLock()
        self._condition = Condition(self._lock)
        self._executor = None
        self._owner_lock = None
        self._scheduled = set()
        self._admitted_operations = 0
        self._retry_reservations = {}
        self._starting = False
        self._started = False
        self._shutdown = False

    def start(self):
        """Initialize persistence, recover interrupted work, and schedule it once."""
        with self._condition:
            while self._starting:
                self._condition.wait()
            if self._shutdown:
                raise RuntimeError("summary task service is shut down")
            if self._started:
                return
            self._starting = True

        owner_lock = _DatabaseOwnerLock(self._database_path)
        try:
            owner_lock.acquire()
            _storage_call(initialize_summary_tables, self._database_path)
            incomplete = _storage_call(
                recover_incomplete_tasks,
                self._database_path,
            )
            executor = ThreadPoolExecutor(
                max_workers=self._max_workers,
                thread_name_prefix="summary-task",
            )
        except Exception:
            owner_lock.release()
            with self._condition:
                self._starting = False
                self._condition.notify_all()
            raise

        with self._condition:
            self._starting = False
            if self._shutdown:
                discard_executor = True
            else:
                discard_executor = False
                self._executor = executor
                self._owner_lock = owner_lock
                self._started = True
                for task in incomplete:
                    self._schedule_locked(task.task_id)
            self._condition.notify_all()
        if discard_executor:
            executor.shutdown(wait=False, cancel_futures=True)
            owner_lock.release()
            raise RuntimeError("summary task service is shut down")

    def shutdown(self):
        """Stop accepting work and cancel jobs that have not started running."""
        with self._condition:
            if self._shutdown:
                return
            self._shutdown = True
            executor = self._executor
            owner_lock = self._take_owner_lock_if_idle_locked()
            self._condition.notify_all()
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
        if owner_lock is not None:
            owner_lock.release()

    def submit(self, video_url):
        """Return reusable work for a video or create and schedule a new task."""
        with self._admitted_operation():
            video_id = extract_bvid(video_url)
            canonical_url = f"https://www.bilibili.com/video/{video_id}"
            active = find_active_task(video_id, self._database_path)
            if active is not None:
                self._schedule(active.task_id)
                return active
            succeeded = find_latest_success(video_id, self._database_path)
            if succeeded is not None:
                return succeeded
            try:
                task = create_summary_task(
                    video_id,
                    canonical_url,
                    self._database_path,
                )
            except RepositoryError:
                active = find_active_task(video_id, self._database_path)
                if active is not None:
                    task = active
                else:
                    succeeded = find_latest_success(video_id, self._database_path)
                    if succeeded is None:
                        raise
                    task = succeeded
            if task.status in {"PENDING", "PROCESSING"}:
                self._schedule(task.task_id)
            return task

    def retry(self, task_id):
        """Create one new linked attempt for a failed task."""
        with self._admitted_operation():
            with self._lock:
                reservation = self._retry_reservations.get(task_id)
                if reservation is None:
                    reservation = {
                        "event": Event(),
                        "task_id": None,
                        "error": None,
                    }
                    self._retry_reservations[task_id] = reservation
                    owns_reservation = True
                else:
                    owns_reservation = False

            if not owns_reservation:
                reservation["event"].wait()
                if reservation["error"] is not None:
                    raise reservation["error"]
                return get_summary_task(
                    reservation["task_id"],
                    self._database_path,
                )

            try:
                failed = get_summary_task(task_id, self._database_path)
                if failed is None:
                    raise ValueError("summary task does not exist")
                if failed.status != "FAILED":
                    raise ValueError("only FAILED summary tasks can be retried")
                active = find_active_task(failed.video_id, self._database_path)
                if active is not None:
                    if active.retry_of == failed.task_id:
                        task = active
                    else:
                        raise RuntimeError("another summary task is already active")
                else:
                    try:
                        task = create_summary_task(
                            failed.video_id,
                            failed.video_url,
                            self._database_path,
                            retry_of=failed.task_id,
                        )
                    except RepositoryError as error:
                        active = find_active_task(
                            failed.video_id,
                            self._database_path,
                        )
                        if active is None or active.retry_of != failed.task_id:
                            raise RuntimeError(
                                "another summary task is already active"
                            ) from error
                        task = active
                self._schedule(task.task_id)
                reservation["task_id"] = task.task_id
                return task
            except Exception as error:
                reservation["error"] = error
                raise
            finally:
                reservation["event"].set()
                with self._lock:
                    if self._retry_reservations.get(task_id) is reservation:
                        del self._retry_reservations[task_id]

    def get(self, task_id):
        """Delegate validated exact task lookup to the repository."""
        return _storage_call(get_summary_task, task_id, self._database_path)

    def list(self, limit=50):
        """Delegate validated newest-first task listing to the repository."""
        return _storage_call(list_summary_tasks, limit, self._database_path)

    def _require_running_locked(self):
        if self._shutdown:
            raise RuntimeError("summary task service is shut down")
        if not self._started:
            raise RuntimeError("summary task service has not been started")

    @contextmanager
    def _admitted_operation(self):
        with self._lock:
            self._require_running_locked()
            self._admitted_operations += 1
        try:
            try:
                yield
            except (sqlite3.Error, OSError) as error:
                raise RuntimeError(
                    "summary task storage is temporarily unavailable"
                ) from error
        finally:
            with self._lock:
                self._admitted_operations -= 1
                owner_lock = self._take_owner_lock_if_idle_locked()
            if owner_lock is not None:
                owner_lock.release()

    def _schedule_locked(
        self,
        task_id,
        recovery=None,
        recovery_reschedules=_WORKER_RECOVERY_RESCHEDULES,
    ):
        if task_id in self._scheduled:
            return
        self._scheduled.add(task_id)
        try:
            future = self._executor.submit(self._run_task, task_id, recovery)
        except Exception:
            self._scheduled.discard(task_id)
            raise

        def complete_scheduled(
            completed,
            scheduled_task_id=task_id,
            retries=recovery_reschedules,
        ):
            self._complete_scheduled(
                scheduled_task_id,
                completed,
                retries,
            )

        future.add_done_callback(complete_scheduled)

    def _schedule(self, task_id):
        with self._lock:
            if self._shutdown:
                return False
            self._schedule_locked(task_id)
            return True

    def _complete_scheduled(self, task_id, future, recovery_reschedules):
        try:
            recovery = future.result()
        except Exception:
            _LOGGER.exception("summary worker stopped after an unexpected error")
            recovery = None

        owner_lock = None
        with self._lock:
            self._scheduled.discard(task_id)
            if recovery is not None and recovery_reschedules > 0 and not self._shutdown:
                self._schedule_locked(
                    task_id,
                    recovery,
                    recovery_reschedules - 1,
                )
                return
            owner_lock = self._take_owner_lock_if_idle_locked()
        if recovery is not None:
            _LOGGER.error(
                "summary worker storage retries exhausted; task %s remains "
                "durable and recoverable",
                task_id,
            )
        if owner_lock is not None:
            owner_lock.release()

    def _take_owner_lock_if_idle_locked(self):
        if not self._shutdown or self._scheduled or self._admitted_operations:
            return None
        owner_lock = self._owner_lock
        self._owner_lock = None
        return owner_lock

    def _run_task(self, task_id, recovery=None):
        if recovery is None or recovery[0] == "execute":
            return self._execute_task(task_id)
        return self._reconcile_transition(recovery)

    def _worker_transition(self, *args):
        last_error = None
        for _attempt in range(_WORKER_STORAGE_ATTEMPTS):
            try:
                return transition_task(*args)
            except (sqlite3.Error, OSError) as error:
                last_error = error
        raise _WorkerStorageError(
            "summary worker storage is temporarily unavailable"
        ) from last_error

    def _reconcile_transition(self, recovery):
        _kind, transition_arguments = recovery
        try:
            self._worker_transition(*transition_arguments)
        except RepositoryError:
            return None
        except _WorkerStorageError:
            return recovery
        return None

    def _execute_task(self, task_id):
        claim_arguments = (
            task_id,
            {"PENDING"},
            "PROCESSING",
            "METADATA",
            None,
            None,
            self._database_path,
        )
        try:
            task = self._worker_transition(*claim_arguments)
        except RepositoryError:
            return None
        except _WorkerStorageError:
            return ("execute", ())
        log_event(
            EVENT_LOGGER,
            "INFO",
            "summary_task_started",
            "视频总结任务开始",
            task_type="summary",
            task_id=task_id,
        )

        def report_stage(stage):
            self._worker_transition(
                task_id,
                {"PROCESSING"},
                "PROCESSING",
                stage,
                None,
                None,
                self._database_path,
            )
            log_event(
                EVENT_LOGGER,
                "INFO",
                "summary_stage_changed",
                f"视频总结进入阶段 {stage}",
                task_type="summary",
                task_id=task_id,
            )

        try:
            result = self._runner(
                task.video_url,
                progress_callback=report_stage,
            )
            result = public_summary_result(result)
        except Exception as error:
            code, message = public_error_from_exception(error)
            failure_arguments = (
                task_id,
                {"PROCESSING"},
                "FAILED",
                "FAILED",
                None,
                f"{code}: {message}",
                self._database_path,
            )
            try:
                self._worker_transition(*failure_arguments)
            except RepositoryError:
                return None
            except _WorkerStorageError:
                return ("transition", failure_arguments)
            log_event(
                EVENT_LOGGER,
                "ERROR",
                "summary_task_failed",
                f"视频总结任务失败：{code}",
                task_type="summary",
                task_id=task_id,
            )
            return None

        success_arguments = (
            task_id,
            {"PROCESSING"},
            "SUCCEEDED",
            "COMPLETE",
            result,
            None,
            self._database_path,
        )
        try:
            self._worker_transition(*success_arguments)
        except RepositoryError:
            return None
        except _WorkerStorageError:
            return ("transition", success_arguments)
        log_event(
            EVENT_LOGGER,
            "INFO",
            "summary_task_succeeded",
            "视频总结任务完成",
            task_type="summary",
            task_id=task_id,
        )
        return None


__all__ = ["SummaryTaskService", "extract_bvid"]
