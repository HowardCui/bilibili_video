import sqlite3
from pathlib import Path
from threading import Barrier, Event, Thread

import pytest

from summarization import summarizer, video_summary
from video_processing import get_metadata, subtitle_parser, video_transcript_pipeline
from web_app.errors import public_error_from_exception, public_summary_result
from web_app.summary import service as summary_service_module
from web_app.summary.repository import (
    create_summary_task,
    get_summary_task,
    initialize_summary_tables,
    transition_task,
)
from web_app.summary.service import SummaryTaskService, extract_bvid

VALID_URL = "https://www.bilibili.com/video/BV1TEST"


@pytest.mark.parametrize(
    ("video_url", "expected"),
    [
        ("https://www.bilibili.com/video/BV1TEST", "BV1TEST"),
        ("https://www.bilibili.com/video/bv1TEST/?spm_id_from=333", "BV1TEST"),
        ("https://b23.tv/BV1TEST", "BV1TEST"),
    ],
)
def test_extract_bvid_normalizes_offline_resolvable_bilibili_urls(
    video_url,
    expected,
):
    assert extract_bvid(video_url) == expected


@pytest.mark.parametrize(
    "video_url",
    [
        "",
        "not a URL",
        "https://example.com/video/BV1TEST",
        "https://www.bilibili.com.example/video/BV1TEST",
        "https://www.bilibili.com/video/av123",
        "https://www.bilibili.com/video/BV1",
        "https://www.bilibili.com/video/BV1TEST/extra",
        "https://b23.tv/AbCdEfG",
    ],
)
def test_extract_bvid_rejects_unsafe_or_missing_video_identifiers(video_url):
    with pytest.raises(ValueError):
        extract_bvid(video_url)


def test_duplicate_submission_reuses_active_task(tmp_path):
    entered = Event()
    release = Event()

    def blocked_runner(video_url, progress_callback):
        entered.set()
        assert release.wait(timeout=5)
        return {"video_id": "BV1TEST", "summary": {"text": "done"}}

    service = SummaryTaskService(tmp_path / "summary.db", blocked_runner, max_workers=1)
    service.start()
    try:
        first = service.submit(VALID_URL)
        assert entered.wait(timeout=5)

        second = service.submit("https://b23.tv/bv1TEST")

        assert second.task_id == first.task_id
        assert second.status in {"PENDING", "PROCESSING"}
    finally:
        release.set()
        service.shutdown()


def test_two_services_cannot_own_and_execute_the_same_database_concurrently(
    tmp_path,
):
    database_path = tmp_path / "summary.db"
    first_entered = Event()
    release_first = Event()
    second_entered = Event()

    def first_runner(video_url, progress_callback):
        first_entered.set()
        assert release_first.wait(timeout=5)
        return {"video_id": "BV1TEST"}

    def second_runner(video_url, progress_callback):
        second_entered.set()
        return {"video_id": "BV1TEST"}

    first_service = SummaryTaskService(database_path, first_runner, max_workers=1)
    second_service = SummaryTaskService(database_path, second_runner, max_workers=1)
    first_service.start()
    first_service.submit(VALID_URL)
    assert first_entered.wait(timeout=5)
    first_service.shutdown()

    try:
        with pytest.raises(RuntimeError, match="already owned"):
            second_service.start()
        assert not second_entered.is_set()
    finally:
        release_first.set()
        second_service.shutdown()


@pytest.mark.parametrize(
    ("method_name", "repository_name", "error"),
    [
        (
            "get",
            "get_summary_task",
            sqlite3.OperationalError("database is locked at C:/private/db"),
        ),
        (
            "list",
            "list_summary_tasks",
            OSError("permission denied for C:/private/db"),
        ),
    ],
)
def test_service_reads_normalize_storage_failures_without_leaking_details(
    monkeypatch,
    tmp_path,
    method_name,
    repository_name,
    error,
):
    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(summary_service_module, repository_name, fail)
    service = SummaryTaskService(
        tmp_path / "summary.db",
        lambda video_url, progress_callback: None,
    )

    with pytest.raises(
        RuntimeError,
        match="storage is temporarily unavailable",
    ) as raised:
        if method_name == "get":
            service.get("task-id")
        else:
            service.list()

    assert raised.value.__cause__ is error
    assert "private" not in str(raised.value)


def test_service_read_does_not_hide_programming_errors(monkeypatch, tmp_path):
    def fail(*_args, **_kwargs):
        raise TypeError("programming contract broken")

    monkeypatch.setattr(summary_service_module, "get_summary_task", fail)
    service = SummaryTaskService(
        tmp_path / "summary.db",
        lambda video_url, progress_callback: None,
    )

    with pytest.raises(TypeError, match="programming contract broken"):
        service.get("task-id")


@pytest.mark.parametrize(
    ("method_name", "repository_name"),
    [
        ("submit", "find_active_task"),
        ("retry", "get_summary_task"),
    ],
)
def test_service_actions_normalize_storage_failures(
    monkeypatch,
    tmp_path,
    method_name,
    repository_name,
):
    service = SummaryTaskService(
        tmp_path / "summary.db",
        lambda video_url, progress_callback: None,
    )
    service.start()

    def fail(*_args, **_kwargs):
        raise sqlite3.OperationalError("database locked at C:/private/db")

    monkeypatch.setattr(summary_service_module, repository_name, fail)
    try:
        with pytest.raises(
            RuntimeError,
            match="storage is temporarily unavailable",
        ) as raised:
            if method_name == "submit":
                service.submit(VALID_URL)
            else:
                service.retry("00000000-0000-0000-0000-000000000000")
        assert "private" not in str(raised.value)
    finally:
        service.shutdown()


def test_submit_reuses_latest_sanitized_success_without_mutating_it(tmp_path):
    first_done = Event()
    barrier_entered = Event()
    release_barrier = Event()

    def runner(video_url, progress_callback):
        video_id = extract_bvid(video_url)
        if video_id == "BV1BARRIER":
            barrier_entered.set()
            assert release_barrier.wait(timeout=5)
            return {"video_id": video_id}
        progress_callback("SUBTITLE")
        progress_callback("TRANSCRIPT")
        first_done.set()
        return {
            "video_id": video_id,
            "summary": {"text": "done"},
            "metadata_path": "C:/private/metadata.json",
        }

    service = SummaryTaskService(tmp_path / "summary.db", runner, max_workers=1)
    service.start()
    try:
        first = service.submit(VALID_URL)
        assert first_done.wait(timeout=5)
        service.submit("https://www.bilibili.com/video/BV1BARRIER")
        assert barrier_entered.wait(timeout=5)

        reused = service.submit("https://www.bilibili.com/video/bv1TEST/")

        assert reused.task_id == first.task_id
        assert reused.status == "SUCCEEDED"
        assert reused.stage == "COMPLETE"
        assert reused.result == {
            "video_id": "BV1TEST",
            "summary": {"text": "done"},
        }
        assert reused.retry_of is None
    finally:
        release_barrier.set()
        service.shutdown()


def test_submit_prefers_active_task_over_prior_success(tmp_path):
    database_path = tmp_path / "summary.db"
    initialize_summary_tables(database_path)
    succeeded = create_summary_task("BV1TEST", VALID_URL, database_path)
    transition_task(
        succeeded.task_id,
        {"PENDING"},
        "PROCESSING",
        "METADATA",
        None,
        None,
        database_path,
    )
    transition_task(
        succeeded.task_id,
        {"PROCESSING"},
        "SUCCEEDED",
        "COMPLETE",
        {"video_id": "BV1TEST", "summary": {"text": "old"}},
        None,
        database_path,
    )
    active = create_summary_task("BV1TEST", VALID_URL, database_path)
    runner_entered = Event()
    release_runner = Event()

    def runner(video_url, progress_callback):
        runner_entered.set()
        assert release_runner.wait(timeout=5)
        return {"video_id": "BV1TEST", "summary": {"text": "new"}}

    service = SummaryTaskService(database_path, runner, max_workers=1)
    service.start()
    try:
        assert runner_entered.wait(timeout=5)
        reused = service.submit(VALID_URL)
        assert reused.task_id == active.task_id
        assert reused.task_id != succeeded.task_id
        assert reused.status in {"PENDING", "PROCESSING"}
    finally:
        release_runner.set()
        service.shutdown()


def test_worker_persists_canonical_callback_stages_and_safe_failure(tmp_path):
    failed_runner_done = Event()
    barrier_entered = Event()
    release_barrier = Event()

    def runner(video_url, progress_callback):
        video_id = extract_bvid(video_url)
        if video_id == "BV1BARRIER":
            barrier_entered.set()
            assert release_barrier.wait(timeout=5)
            return {"video_id": video_id}
        progress_callback("SUBTITLE")
        progress_callback("TRANSCRIPT")
        failed_runner_done.set()
        raise RuntimeError("api_key=secret at C:/private/provider.py")

    service = SummaryTaskService(tmp_path / "summary.db", runner, max_workers=1)
    service.start()
    try:
        failed = service.submit(VALID_URL)
        assert failed_runner_done.wait(timeout=5)
        service.submit("https://www.bilibili.com/video/BV1BARRIER")
        assert barrier_entered.wait(timeout=5)

        stored = service.get(failed.task_id)

        assert stored.status == "FAILED"
        assert stored.stage == "FAILED"
        assert stored.error == (
            "SUMMARY_FAILED: The video summary could not be completed."
        )
    finally:
        release_barrier.set()
        service.shutdown()


def test_worker_retries_transient_claim_storage_failure(monkeypatch, tmp_path):
    real_transition = summary_service_module.transition_task
    failed_once = False
    terminal_persisted = Event()
    runner_calls = []

    def transient_transition(*args, **kwargs):
        nonlocal failed_once
        expected_statuses, new_status = args[1], args[2]
        if expected_statuses == {"PENDING"} and not failed_once:
            failed_once = True
            raise sqlite3.OperationalError("locked at C:/private/summary.db")
        record = real_transition(*args, **kwargs)
        if new_status == "SUCCEEDED":
            terminal_persisted.set()
        return record

    monkeypatch.setattr(
        summary_service_module,
        "transition_task",
        transient_transition,
    )

    def runner(video_url, progress_callback):
        runner_calls.append(video_url)
        return {"video_id": "BV1TEST"}

    service = SummaryTaskService(tmp_path / "summary.db", runner, max_workers=1)
    service.start()
    task = service.submit(VALID_URL)
    try:
        assert terminal_persisted.wait(timeout=5)
        assert service.get(task.task_id).status == "SUCCEEDED"
        assert runner_calls == [VALID_URL]
    finally:
        service.shutdown()


def test_worker_retries_transient_progress_storage_failure(monkeypatch, tmp_path):
    real_transition = summary_service_module.transition_task
    failed_once = False
    terminal_persisted = Event()
    runner_calls = []

    def transient_transition(*args, **kwargs):
        nonlocal failed_once
        expected_statuses, new_status, stage = args[1], args[2], args[3]
        if (
            expected_statuses == {"PROCESSING"}
            and new_status == "PROCESSING"
            and stage == "SUBTITLE"
            and not failed_once
        ):
            failed_once = True
            raise sqlite3.OperationalError("locked at C:/private/summary.db")
        record = real_transition(*args, **kwargs)
        if new_status == "SUCCEEDED":
            terminal_persisted.set()
        return record

    monkeypatch.setattr(
        summary_service_module,
        "transition_task",
        transient_transition,
    )

    def runner(video_url, progress_callback):
        runner_calls.append(video_url)
        progress_callback("SUBTITLE")
        return {"video_id": "BV1TEST"}

    service = SummaryTaskService(tmp_path / "summary.db", runner, max_workers=1)
    service.start()
    task = service.submit(VALID_URL)
    try:
        assert terminal_persisted.wait(timeout=5)
        assert service.get(task.task_id).status == "SUCCEEDED"
        assert runner_calls == [VALID_URL]
    finally:
        service.shutdown()


def test_worker_reconciles_success_without_rerunning_runner(monkeypatch, tmp_path):
    real_transition = summary_service_module.transition_task
    failed_once = False
    terminal_persisted = Event()
    runner_calls = []

    def transient_transition(*args, **kwargs):
        nonlocal failed_once
        new_status = args[2]
        if new_status == "SUCCEEDED" and not failed_once:
            failed_once = True
            raise sqlite3.OperationalError("locked at C:/private/summary.db")
        record = real_transition(*args, **kwargs)
        if new_status == "SUCCEEDED":
            terminal_persisted.set()
        return record

    monkeypatch.setattr(
        summary_service_module,
        "transition_task",
        transient_transition,
    )

    def runner(video_url, progress_callback):
        runner_calls.append(video_url)
        return {"video_id": "BV1TEST", "summary": {"text": "done"}}

    service = SummaryTaskService(tmp_path / "summary.db", runner, max_workers=1)
    service.start()
    task = service.submit(VALID_URL)
    try:
        assert terminal_persisted.wait(timeout=5)
        stored = service.get(task.task_id)
        assert stored.status == "SUCCEEDED"
        assert stored.result["summary"] == {"text": "done"}
        assert runner_calls == [VALID_URL]
    finally:
        service.shutdown()


def test_worker_reconciles_failure_without_rerunning_runner(monkeypatch, tmp_path):
    real_transition = summary_service_module.transition_task
    failed_once = False
    terminal_persisted = Event()
    runner_calls = []

    def transient_transition(*args, **kwargs):
        nonlocal failed_once
        new_status = args[2]
        if new_status == "FAILED" and not failed_once:
            failed_once = True
            raise OSError("unwritable C:/private/summary.db")
        record = real_transition(*args, **kwargs)
        if new_status == "FAILED":
            terminal_persisted.set()
        return record

    monkeypatch.setattr(
        summary_service_module,
        "transition_task",
        transient_transition,
    )

    def runner(video_url, progress_callback):
        runner_calls.append(video_url)
        raise RuntimeError("provider cookie=secret")

    service = SummaryTaskService(tmp_path / "summary.db", runner, max_workers=1)
    service.start()
    task = service.submit(VALID_URL)
    try:
        assert terminal_persisted.wait(timeout=5)
        stored = service.get(task.task_id)
        assert stored.status == "FAILED"
        assert stored.error == (
            "SUMMARY_FAILED: The video summary could not be completed."
        )
        assert runner_calls == [VALID_URL]
    finally:
        service.shutdown()


def test_worker_bounds_persistent_claim_failures_and_keeps_pending_recoverable(
    monkeypatch,
    tmp_path,
    caplog,
):
    transition_calls = 0
    retries_exhausted = Event()

    def unavailable_transition(*_args, **_kwargs):
        nonlocal transition_calls
        transition_calls += 1
        if transition_calls == 9:
            retries_exhausted.set()
        raise sqlite3.OperationalError("locked at C:/private/summary.db")

    monkeypatch.setattr(
        summary_service_module,
        "transition_task",
        unavailable_transition,
    )
    service = SummaryTaskService(
        tmp_path / "summary.db",
        lambda video_url, progress_callback: pytest.fail("runner must not execute"),
        max_workers=1,
    )
    service.start()
    task = service.submit(VALID_URL)
    try:
        assert retries_exhausted.wait(timeout=5)
        assert transition_calls == 9
        assert service.get(task.task_id).status == "PENDING"
        assert "remains durable and recoverable" in caplog.text
        assert "private" not in caplog.text
    finally:
        service.shutdown()


def test_start_recovers_each_incomplete_task_once_and_is_idempotent(tmp_path):
    database_path = tmp_path / "summary.db"
    initialize_summary_tables(database_path)
    pending = create_summary_task(
        "BV1PENDING",
        "https://www.bilibili.com/video/BV1PENDING",
        database_path,
    )
    processing = create_summary_task(
        "BV1PROCESSING",
        "https://www.bilibili.com/video/BV1PROCESSING",
        database_path,
    )
    transition_task(
        processing.task_id,
        {"PENDING"},
        "PROCESSING",
        "MERGE",
        None,
        None,
        database_path,
    )
    both_entered = Barrier(3)
    release = Event()
    calls = []

    def runner(video_url, progress_callback):
        calls.append(extract_bvid(video_url))
        both_entered.wait(timeout=5)
        assert release.wait(timeout=5)
        return {"video_id": extract_bvid(video_url)}

    service = SummaryTaskService(database_path, runner, max_workers=2)
    service.start()
    service.start()
    try:
        both_entered.wait(timeout=5)
        assert sorted(calls) == ["BV1PENDING", "BV1PROCESSING"]
        assert service.get(pending.task_id).status == "PROCESSING"
        assert service.get(processing.task_id).status == "PROCESSING"
    finally:
        release.set()
        service.shutdown()


def test_start_normalizes_storage_failure_and_releases_database_ownership(
    monkeypatch,
    tmp_path,
):
    database_path = tmp_path / "summary.db"
    real_initialize = summary_service_module.initialize_summary_tables

    def fail(_database_path):
        raise sqlite3.OperationalError("database locked at C:/private/db")

    monkeypatch.setattr(
        summary_service_module,
        "initialize_summary_tables",
        fail,
    )
    failed_service = SummaryTaskService(
        database_path,
        lambda video_url, progress_callback: None,
    )

    with pytest.raises(
        RuntimeError,
        match="storage is temporarily unavailable",
    ) as raised:
        failed_service.start()
    assert "private" not in str(raised.value)

    monkeypatch.setattr(
        summary_service_module,
        "initialize_summary_tables",
        real_initialize,
    )
    replacement = SummaryTaskService(
        database_path,
        lambda video_url, progress_callback: None,
    )
    replacement.start()
    replacement.shutdown()


def test_shutdown_rejects_new_work_and_preserves_queued_task_for_recovery(tmp_path):
    first_entered = Event()
    release_first = Event()

    def runner(video_url, progress_callback):
        first_entered.set()
        assert release_first.wait(timeout=5)
        return {"video_id": extract_bvid(video_url)}

    database_path = tmp_path / "summary.db"
    service = SummaryTaskService(database_path, runner, max_workers=1)
    service.start()
    first = service.submit(VALID_URL)
    assert first_entered.wait(timeout=5)
    queued = service.submit("https://www.bilibili.com/video/BV1QUEUED")

    service.shutdown()
    service.shutdown()

    try:
        with pytest.raises(RuntimeError, match="shut down"):
            service.submit("https://www.bilibili.com/video/BV1NEW")
        assert get_summary_task(first.task_id, database_path).status == "PROCESSING"
        assert get_summary_task(queued.task_id, database_path).status == "PENDING"
    finally:
        release_first.set()


def test_concurrent_retry_creates_one_linked_task_and_preserves_failure(tmp_path):
    database_path = tmp_path / "summary.db"
    initialize_summary_tables(database_path)
    failed = create_summary_task("BV1FAILED", VALID_URL, database_path)
    transition_task(
        failed.task_id,
        {"PENDING"},
        "PROCESSING",
        "METADATA",
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
        "SUMMARY_FAILED: The video summary could not be completed.",
        database_path,
    )
    runner_entered = Event()
    release = Event()

    def runner(video_url, progress_callback):
        runner_entered.set()
        assert release.wait(timeout=5)
        return {"video_id": "BV1FAILED"}

    service = SummaryTaskService(database_path, runner, max_workers=1)
    service.start()
    callers_ready = Barrier(3)
    retried = []

    def call_retry():
        callers_ready.wait(timeout=5)
        retried.append(service.retry(failed.task_id))

    threads = [Thread(target=call_retry), Thread(target=call_retry)]
    for thread in threads:
        thread.start()
    callers_ready.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()
    try:
        assert runner_entered.wait(timeout=5)
        assert len({task.task_id for task in retried}) == 1
        retry = retried[0]
        assert retry.task_id != failed.task_id
        assert retry.retry_of == failed.task_id
        assert service.get(failed.task_id).status == "FAILED"
        assert service.get(failed.task_id).attempt_number == 1
        assert retry.attempt_number == 2
    finally:
        release.set()
        service.shutdown()


def test_retry_rejects_non_failed_and_missing_tasks(tmp_path):
    release = Event()
    service = SummaryTaskService(
        tmp_path / "summary.db",
        lambda video_url, progress_callback: release.wait(timeout=5),
        max_workers=1,
    )
    service.start()
    active = service.submit(VALID_URL)
    try:
        with pytest.raises(ValueError, match="FAILED"):
            service.retry(active.task_id)
        with pytest.raises(ValueError, match="does not exist"):
            service.retry("00000000-0000-0000-0000-000000000000")
    finally:
        release.set()
        service.shutdown()


def test_retry_does_not_relabel_an_unrelated_active_submission(tmp_path):
    database_path = tmp_path / "summary.db"
    initialize_summary_tables(database_path)
    failed = create_summary_task("BV1TEST", VALID_URL, database_path)
    transition_task(
        failed.task_id,
        {"PENDING"},
        "PROCESSING",
        "METADATA",
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
        "SUMMARY_FAILED: The video summary could not be completed.",
        database_path,
    )
    release = Event()
    service = SummaryTaskService(
        database_path,
        lambda video_url, progress_callback: release.wait(timeout=5),
        max_workers=1,
    )
    service.start()
    unrelated = service.submit(VALID_URL)
    try:
        with pytest.raises(RuntimeError, match="already active"):
            service.retry(failed.task_id)
        assert unrelated.retry_of is None
    finally:
        release.set()
        service.shutdown()


def test_shutdown_does_not_wait_for_submit_repository_io(monkeypatch, tmp_path):
    lookup_entered = Event()
    release_lookup = Event()
    shutdown_done = Event()
    runner_entered = Event()
    real_find_active = summary_service_module.find_active_task

    def blocked_find_active(video_id, database_path):
        lookup_entered.set()
        assert release_lookup.wait(timeout=5)
        return real_find_active(video_id, database_path)

    service = SummaryTaskService(
        tmp_path / "summary.db",
        lambda video_url, progress_callback: runner_entered.set(),
        max_workers=1,
    )
    service.start()
    monkeypatch.setattr(
        summary_service_module,
        "find_active_task",
        blocked_find_active,
    )
    submitted = []
    submit_errors = []

    def submit():
        try:
            submitted.append(service.submit(VALID_URL))
        except Exception as error:
            submit_errors.append(error)

    submit_thread = Thread(target=submit)
    shutdown_thread = Thread(target=lambda: (service.shutdown(), shutdown_done.set()))
    submit_thread.start()
    assert lookup_entered.wait(timeout=5)
    shutdown_thread.start()
    shutdown_was_independent = shutdown_done.wait(timeout=1)
    release_lookup.set()
    submit_thread.join(timeout=5)
    shutdown_thread.join(timeout=5)

    assert shutdown_was_independent
    assert not submit_thread.is_alive()
    assert not shutdown_thread.is_alive()
    assert submit_errors == []
    assert len(submitted) == 1
    assert submitted[0].status == "PENDING"
    assert not runner_entered.is_set()


def test_shutdown_keeps_ownership_while_admitted_submit_is_in_repository_io(
    monkeypatch,
    tmp_path,
):
    database_path = tmp_path / "summary.db"
    lookup_entered = Event()
    release_lookup = Event()
    replacement_entered = Event()
    release_replacement = Event()
    real_find_active = summary_service_module.find_active_task

    def blocked_find_active(video_id, path):
        lookup_entered.set()
        assert release_lookup.wait(timeout=5)
        return real_find_active(video_id, path)

    first_service = SummaryTaskService(
        database_path,
        lambda video_url, progress_callback: None,
    )
    first_service.start()
    monkeypatch.setattr(
        summary_service_module,
        "find_active_task",
        blocked_find_active,
    )
    submitted = []
    submit_errors = []

    def submit():
        try:
            submitted.append(first_service.submit(VALID_URL))
        except Exception as error:
            submit_errors.append(error)

    submit_thread = Thread(target=submit)
    submit_thread.start()
    assert lookup_entered.wait(timeout=5)
    first_service.shutdown()

    blocked_replacement = SummaryTaskService(
        database_path,
        lambda video_url, progress_callback: None,
    )
    with pytest.raises(RuntimeError, match="already owned"):
        blocked_replacement.start()
    blocked_replacement.shutdown()

    release_lookup.set()
    submit_thread.join(timeout=5)
    assert not submit_thread.is_alive()
    assert submit_errors == []
    assert len(submitted) == 1
    assert submitted[0].status == "PENDING"

    monkeypatch.setattr(
        summary_service_module,
        "find_active_task",
        real_find_active,
    )

    def recovered_runner(video_url, progress_callback):
        replacement_entered.set()
        assert release_replacement.wait(timeout=5)
        return {"video_id": "BV1TEST"}

    replacement = SummaryTaskService(database_path, recovered_runner)
    replacement.start()
    try:
        assert replacement_entered.wait(timeout=5)
    finally:
        release_replacement.set()
        replacement.shutdown()


def test_shutdown_keeps_ownership_while_admitted_retry_is_in_repository_io(
    monkeypatch,
    tmp_path,
):
    database_path = tmp_path / "summary.db"
    initialize_summary_tables(database_path)
    failed = create_summary_task("BV1FAILED", VALID_URL, database_path)
    transition_task(
        failed.task_id,
        {"PENDING"},
        "PROCESSING",
        "METADATA",
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
        "SUMMARY_FAILED: The video summary could not be completed.",
        database_path,
    )
    lookup_entered = Event()
    release_lookup = Event()
    replacement_entered = Event()
    release_replacement = Event()
    real_get_task = summary_service_module.get_summary_task

    def blocked_get_task(task_id, path):
        lookup_entered.set()
        assert release_lookup.wait(timeout=5)
        return real_get_task(task_id, path)

    first_service = SummaryTaskService(
        database_path,
        lambda video_url, progress_callback: None,
    )
    first_service.start()
    monkeypatch.setattr(
        summary_service_module,
        "get_summary_task",
        blocked_get_task,
    )
    retried = []
    retry_errors = []

    def retry():
        try:
            retried.append(first_service.retry(failed.task_id))
        except Exception as error:
            retry_errors.append(error)

    retry_thread = Thread(target=retry)
    retry_thread.start()
    assert lookup_entered.wait(timeout=5)
    first_service.shutdown()

    blocked_replacement = SummaryTaskService(
        database_path,
        lambda video_url, progress_callback: None,
    )
    with pytest.raises(RuntimeError, match="already owned"):
        blocked_replacement.start()
    blocked_replacement.shutdown()

    release_lookup.set()
    retry_thread.join(timeout=5)
    assert not retry_thread.is_alive()
    assert retry_errors == []
    assert len(retried) == 1
    assert retried[0].status == "PENDING"
    assert retried[0].retry_of == failed.task_id

    monkeypatch.setattr(
        summary_service_module,
        "get_summary_task",
        real_get_task,
    )

    def recovered_runner(video_url, progress_callback):
        replacement_entered.set()
        assert release_replacement.wait(timeout=5)
        return {"video_id": "BV1FAILED"}

    replacement = SummaryTaskService(database_path, recovered_runner)
    replacement.start()
    try:
        assert replacement_entered.wait(timeout=5)
    finally:
        release_replacement.set()
        replacement.shutdown()


def test_retry_race_rejects_an_unrelated_active_submission(monkeypatch, tmp_path):
    database_path = tmp_path / "summary.db"
    initialize_summary_tables(database_path)
    failed = create_summary_task("BV1TEST", VALID_URL, database_path)
    transition_task(
        failed.task_id,
        {"PENDING"},
        "PROCESSING",
        "METADATA",
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
        "SUMMARY_FAILED: The video summary could not be completed.",
        database_path,
    )
    retry_create_entered = Event()
    allow_retry_create = Event()
    runner_entered = Event()
    release_runner = Event()
    real_create_task = summary_service_module.create_summary_task

    def controlled_create_task(*args, **kwargs):
        if kwargs.get("retry_of") is not None:
            retry_create_entered.set()
            assert allow_retry_create.wait(timeout=5)
        return real_create_task(*args, **kwargs)

    monkeypatch.setattr(
        summary_service_module,
        "create_summary_task",
        controlled_create_task,
    )

    def runner(video_url, progress_callback):
        runner_entered.set()
        assert release_runner.wait(timeout=5)
        return {"video_id": "BV1TEST"}

    service = SummaryTaskService(database_path, runner, max_workers=1)
    service.start()
    retry_results = []
    retry_errors = []

    def retry():
        try:
            retry_results.append(service.retry(failed.task_id))
        except Exception as error:
            retry_errors.append(error)

    retry_thread = Thread(target=retry)
    retry_thread.start()
    assert retry_create_entered.wait(timeout=5)
    unrelated = service.submit(VALID_URL)
    assert runner_entered.wait(timeout=5)
    allow_retry_create.set()
    retry_thread.join(timeout=5)
    try:
        assert not retry_thread.is_alive()
        assert retry_results == []
        assert len(retry_errors) == 1
        assert isinstance(retry_errors[0], RuntimeError)
        assert "already active" in str(retry_errors[0])
        assert unrelated.retry_of is None
    finally:
        release_runner.set()
        service.shutdown()


def test_shutdown_does_not_wait_for_start_repository_io(monkeypatch, tmp_path):
    recovery_entered = Event()
    release_recovery = Event()
    shutdown_done = Event()
    real_recover = summary_service_module.recover_incomplete_tasks

    def blocked_recover(database_path):
        recovery_entered.set()
        assert release_recovery.wait(timeout=5)
        return real_recover(database_path)

    monkeypatch.setattr(
        summary_service_module,
        "recover_incomplete_tasks",
        blocked_recover,
    )
    service = SummaryTaskService(
        tmp_path / "summary.db",
        lambda video_url, progress_callback: None,
    )
    start_errors = []

    def start():
        try:
            service.start()
        except Exception as error:
            start_errors.append(error)

    start_thread = Thread(target=start)
    shutdown_thread = Thread(target=lambda: (service.shutdown(), shutdown_done.set()))
    start_thread.start()
    assert recovery_entered.wait(timeout=5)
    shutdown_thread.start()
    shutdown_was_independent = shutdown_done.wait(timeout=1)
    release_recovery.set()
    start_thread.join(timeout=5)
    shutdown_thread.join(timeout=5)

    assert shutdown_was_independent
    assert not start_thread.is_alive()
    assert not shutdown_thread.is_alive()
    assert len(start_errors) == 1
    assert isinstance(start_errors[0], RuntimeError)
    assert "shut down" in str(start_errors[0])


def test_worker_persists_callback_stage_before_runner_continues(tmp_path):
    stage_persisted = Event()
    release_runner = Event()

    def runner(video_url, progress_callback):
        progress_callback("MERGE")
        stage_persisted.set()
        assert release_runner.wait(timeout=5)
        return {"video_id": "BV1TEST"}

    service = SummaryTaskService(tmp_path / "summary.db", runner, max_workers=1)
    service.start()
    task = service.submit(VALID_URL)
    try:
        assert stage_persisted.wait(timeout=5)
        processing = service.get(task.task_id)
        assert processing.status == "PROCESSING"
        assert processing.stage == "MERGE"
    finally:
        release_runner.set()
        service.shutdown()


def test_shutdown_queued_task_is_recovered_once_by_a_new_service(
    monkeypatch,
    tmp_path,
):
    database_path = tmp_path / "summary.db"
    first_runner_entered = Event()
    release_first_runner = Event()
    first_terminal = Event()
    old_queued_ran = Event()
    real_transition = summary_service_module.transition_task
    first_task_id = [None]

    def observed_transition(*args, **kwargs):
        record = real_transition(*args, **kwargs)
        new_status = args[2]
        if args[0] == first_task_id[0] and new_status in {"SUCCEEDED", "FAILED"}:
            first_terminal.set()
        return record

    monkeypatch.setattr(
        summary_service_module,
        "transition_task",
        observed_transition,
    )

    def first_runner(video_url, progress_callback):
        if extract_bvid(video_url) == "BV1QUEUED":
            old_queued_ran.set()
            return {"video_id": "BV1QUEUED"}
        first_runner_entered.set()
        assert release_first_runner.wait(timeout=5)
        return {"video_id": "BV1TEST"}

    first_service = SummaryTaskService(database_path, first_runner, max_workers=1)
    first_service.start()
    first = first_service.submit(VALID_URL)
    first_task_id[0] = first.task_id
    assert first_runner_entered.wait(timeout=5)
    queued = first_service.submit("https://www.bilibili.com/video/BV1QUEUED")
    first_service.shutdown()
    release_first_runner.set()
    assert first_terminal.wait(timeout=5)
    assert not old_queued_ran.is_set()
    assert get_summary_task(queued.task_id, database_path).status == "PENDING"

    recovered_entered = Event()
    release_recovered = Event()
    recovered_calls = []

    def recovered_runner(video_url, progress_callback):
        recovered_calls.append(extract_bvid(video_url))
        recovered_entered.set()
        assert release_recovered.wait(timeout=5)
        return {"video_id": "BV1QUEUED"}

    recovered_service = SummaryTaskService(
        database_path,
        recovered_runner,
        max_workers=1,
    )
    recovered_service.start()
    recovered_service.start()
    try:
        assert recovered_entered.wait(timeout=5)
        assert recovered_calls == ["BV1QUEUED"]
        assert recovered_service.get(queued.task_id).status == "PROCESSING"
    finally:
        release_recovered.set()
        recovered_service.shutdown()


def test_get_and_list_delegate_repository_validation(tmp_path):
    service = SummaryTaskService(
        tmp_path / "summary.db",
        lambda video_url, progress_callback: {"video_id": "BV1TEST"},
    )
    service.start()
    try:
        with pytest.raises(ValueError, match="UUID"):
            service.get("not-a-uuid")
        with pytest.raises(TypeError, match="integer"):
            service.list(True)
        with pytest.raises(ValueError, match="greater than 0"):
            service.list(0)
    finally:
        service.shutdown()


def test_summary_service_uses_real_artifacts_and_reports_stage_boundaries(
    monkeypatch,
    tmp_path,
):
    stages = []
    expected_stages = [
        "METADATA",
        "SUBTITLE",
        "TRANSCRIPT",
        "SPLIT",
        "SUMMARIZE_CHUNKS",
        "MERGE",
    ]
    subtitle_path = tmp_path / "BV1TEST.ai-zh.srt"

    monkeypatch.setattr(get_metadata, "METADATA_DIR", tmp_path / "metadata")
    monkeypatch.setattr(
        subtitle_parser,
        "TRANSCRIPT_DIR",
        tmp_path / "transcripts",
    )
    monkeypatch.setattr(summarizer, "SUMMARY_DIR", tmp_path / "summaries")

    monkeypatch.setattr(
        video_transcript_pipeline,
        "get_video_metadata",
        lambda url, return_raw_info: _metadata_response(stages),
    )
    monkeypatch.setattr(
        video_transcript_pipeline,
        "download_subtitle",
        lambda **kwargs: _download_subtitle(subtitle_path, stages),
    )

    original_save_transcript = video_transcript_pipeline.save_transcript

    def traced_save_transcript(*, subtitle_path):
        assert stages == expected_stages[:3]
        return original_save_transcript(subtitle_path=subtitle_path)

    monkeypatch.setattr(
        video_transcript_pipeline,
        "save_transcript",
        traced_save_transcript,
    )

    original_split_transcript = video_summary.split_transcript

    def traced_split_transcript(segments, max_characters):
        assert stages == expected_stages[:4]
        return original_split_transcript(segments, max_characters)

    monkeypatch.setattr(
        video_summary,
        "split_transcript",
        traced_split_transcript,
    )

    monkeypatch.setattr(
        summarizer,
        "create_model",
        lambda: _FakeSummaryModel(stages, expected_stages),
    )

    result = video_summary.summarize_bilibili_video(
        "https://www.bilibili.com/video/BV1TEST",
        progress_callback=stages.append,
    )

    assert stages == expected_stages
    assert result["video_id"] == "BV1TEST"
    assert result["chunk_count"] == 1
    assert result["summary"]["summary"] == "complete"
    assert Path(result["metadata_path"]).is_file()
    assert Path(result["subtitle_path"]).is_file()
    assert Path(result["transcript_path"]).is_file()
    assert Path(result["summary_path"]).is_file()
    assert public_summary_result(result) == {
        "video_id": "BV1TEST",
        "chunk_count": 1,
        "summary": {
            "summary": "complete",
            "key_points": ["point"],
            "keywords": ["keyword"],
            "video_id": "BV1TEST",
        },
        "elapsed_seconds": result["elapsed_seconds"],
    }


def _metadata_response(stages):
    assert stages == ["METADATA"]
    return (
        {"video_id": "BV1TEST", "subtitle_languages": ["ai-zh"]},
        {"id": "BV1TEST"},
    )


def _download_subtitle(subtitle_path, stages):
    assert stages == ["METADATA", "SUBTITLE"]
    subtitle_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nFirst segment.\n",
        encoding="utf-8",
    )
    return subtitle_path


class _FakeSummaryModel:
    def __init__(self, stages, expected_stages):
        self.stages = stages
        self.expected_stages = expected_stages

    def with_structured_output(self, schema, include_raw):
        return _FakeStructuredModel(schema, self.stages, self.expected_stages)


class _FakeStructuredModel:
    def __init__(self, schema, stages, expected_stages):
        self.schema = schema
        self.stages = stages
        self.expected_stages = expected_stages

    def __ror__(self, prompt):
        return self

    def __call__(self, payload):
        return self.invoke(payload)

    def invoke(self, payload):
        if self.schema is summarizer.ChunkSummary:
            assert self.stages == self.expected_stages[:5]
            parsed = summarizer.ChunkSummary(
                summary="part",
                key_points=["point", "detail"],
                keywords=["keyword", "topic", "video"],
            )
        else:
            assert self.stages == self.expected_stages
            parsed = summarizer.VideoSummary(
                summary="complete",
                key_points=["point"],
                keywords=["keyword"],
            )
        return {"parsed": parsed, "parsing_error": None}


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (
            video_transcript_pipeline.NoChineseSubtitleError("cookie=secret"),
            "NO_CHINESE_SUBTITLE",
        ),
        (
            video_transcript_pipeline.VideoUnavailableError("C:/private/video"),
            "VIDEO_UNAVAILABLE",
        ),
        (video_summary.ModelUnavailableError("api_key=secret"), "MODEL_UNAVAILABLE"),
        (RuntimeError("provider traceback at /private/path"), "SUMMARY_FAILED"),
    ],
)
def test_public_errors_use_stable_codes_without_internal_details(error, expected_code):
    code, message = public_error_from_exception(error)

    assert code == expected_code
    assert "secret" not in message.lower()
    assert "private" not in message.lower()
    assert "traceback" not in message.lower()


def test_public_summary_result_recursively_excludes_local_paths():
    result = {
        "video_id": "BV1TEST",
        "chunk_count": 2,
        "elapsed_seconds": 1.25,
        "metadata_path": "C:\\private\\metadata.json",
        "summary": {
            "text": "safe",
            "source": {"transcript_path": "/private/transcript.json"},
            "items": [
                {"summary_path": Path("C:/private/summary.json")},
                {"label": "keep"},
            ],
        },
    }

    public_result = public_summary_result(result)

    assert public_result == {
        "video_id": "BV1TEST",
        "chunk_count": 2,
        "elapsed_seconds": 1.25,
        "summary": {"text": "safe", "source": {}, "items": [{}, {"label": "keep"}]},
    }


@pytest.mark.parametrize(
    "local_path",
    [
        r"\Users\example\secret.txt",
        r"C:Users\example\secret.txt",
        "D:private.json",
        "data/transcript.json",
        "data\\transcript.json",
    ],
)
def test_public_summary_result_removes_local_path_values_under_arbitrary_keys(
    local_path,
):
    public_result = public_summary_result(
        {
            "video_id": "BV1TEST",
            "summary": {
                "internal_detail": local_path,
                "topic": "data",
                "prose": "The data point is useful.",
                "colon_prose": "note: value",
                "time": "12:30",
                "source_url": "https://example.com/data/transcript.json",
            },
        }
    )

    assert public_result == {
        "video_id": "BV1TEST",
        "summary": {
            "prose": "The data point is useful.",
            "topic": "data",
            "colon_prose": "note: value",
            "time": "12:30",
            "source_url": "https://example.com/data/transcript.json",
        },
    }


def test_public_summary_result_removes_nested_secrets_and_raw_diagnostics():
    result = {
        "video_id": "BV1TEST",
        "video_info": {
            "title": "Safe title",
            "apiKey": "sk-secret",
            "set-cookie": "session=secret",
            "raw_provider_response": {"status": 401, "body": "private"},
        },
        "summary": {
            "summary": (
                "This tutorial explains API keys, cookies, and traceback handling "
                "without publishing credentials."
            ),
            "api_key": "sk-secret",
            "authorization": "Bearer secret",
            "access-token": "token-secret",
            "provider_error": "OpenAI 401 request_id=private",
            "provider_details": {"raw": "OpenAI 401 request_id=private"},
            "traceback": "Traceback (most recent call last):\n  File '/private/a.py'",
            "embedded_credential": "cookie=session-secret",
            "embedded_trace": (
                "Traceback (most recent call last):\n"
                '  File "C:/private/provider.py", line 3'
            ),
        },
    }

    public_result = public_summary_result(result)

    assert public_result == {
        "video_id": "BV1TEST",
        "video_info": {"title": "Safe title"},
        "summary": {
            "summary": (
                "This tutorial explains API keys, cookies, and traceback handling "
                "without publishing credentials."
            )
        },
    }
    assert "secret" not in str(public_result).lower()
    assert "private" not in str(public_result).lower()


def test_public_summary_result_converts_json_compatible_containers_safely():
    result = {
        "video_id": "BV1TEST",
        "summary": {
            "key_points": (
                "keep",
                {"point": "also keep", "refresh_token": "secret"},
                "Authorization: Bearer token-secret",
            ),
            "metrics": (1, True, None),
        },
    }

    public_result = public_summary_result(result)

    assert public_result == {
        "video_id": "BV1TEST",
        "summary": {
            "key_points": ["keep", {"point": "also keep"}],
            "metrics": [1, True],
        },
    }


def test_model_output_validation_failure_is_not_reported_as_an_outage(monkeypatch):
    monkeypatch.setattr(
        video_summary,
        "process_video",
        lambda url, progress_callback=None: {
            "video_id": "BV1TEST",
            "transcript_path": "transcript.json",
        },
    )
    monkeypatch.setattr(
        video_summary,
        "load_transcript",
        lambda path: {"segments": [{"text": "segment"}]},
    )
    monkeypatch.setattr(
        video_summary,
        "split_transcript",
        lambda segments, max_characters: [{"chunk_index": 1, "text": "segment"}],
    )
    monkeypatch.setattr(
        video_summary,
        "summarize_chunks",
        lambda chunks, max_workers: (_ for _ in ()).throw(
            RuntimeError("model output validation failed")
        ),
    )

    with pytest.raises(video_summary.SummaryFailedError) as raised:
        video_summary.summarize_bilibili_video("https://www.bilibili.com/video/BV1TEST")

    assert public_error_from_exception(raised.value)[0] == "SUMMARY_FAILED"


def test_unavailable_advertised_subtitle_is_a_no_subtitle_error(monkeypatch):
    monkeypatch.setattr(
        video_transcript_pipeline,
        "get_video_metadata",
        lambda url, return_raw_info: (
            {"video_id": "BV1TEST", "subtitle_languages": ["zh-CN"]},
            {"id": "BV1TEST"},
        ),
    )
    monkeypatch.setattr(
        video_transcript_pipeline,
        "save_metadata",
        lambda metadata: Path("C:/work/metadata.json"),
    )
    monkeypatch.setattr(
        video_transcript_pipeline,
        "select_subtitle_language",
        lambda languages: "zh-CN",
    )
    monkeypatch.setattr(
        video_transcript_pipeline,
        "download_subtitle",
        lambda **kwargs: (_ for _ in ()).throw(FileNotFoundError("missing subtitle")),
    )

    with pytest.raises(video_transcript_pipeline.NoChineseSubtitleError) as raised:
        video_transcript_pipeline.process_video(
            "https://www.bilibili.com/video/BV1TEST"
        )

    assert public_error_from_exception(raised.value)[0] == "NO_CHINESE_SUBTITLE"
