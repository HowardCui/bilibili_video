import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from web_app import app as app_module
from web_app.summary import server as summary_server_module
from web_app.summary import service as summary_service_module
from web_app.summary import ui as summary_ui_module
from web_app.summary.server import (
    poll_summary_task,
    retry_summary_action,
    submit_summary_action,
    summary_selection_update,
)
from web_app.summary.ui import (
    build_summary_ui,
    summary_result_sections,
    summary_task_view_model,
)


def _task(**overrides):
    values = {
        "task_id": "task-1",
        "video_id": "BV1TEST",
        "video_url": "https://www.bilibili.com/video/BV1TEST",
        "status": "PENDING",
        "stage": None,
        "result": None,
        "error": None,
        "retry_of": None,
        "created_at": None,
        "started_at": None,
        "updated_at": None,
        "finished_at": None,
        "attempt_number": 1,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_processing_task_has_loading_state_and_canonical_stage_label():
    task = _task(
        task_id="task-processing",
        status="PROCESSING",
        stage="SUBTITLE",
    )

    view = summary_task_view_model(task)

    assert view["animation_state"] == "processing"
    assert view["stage_label"] == "正在获取中文字幕"
    assert view["can_retry"] is False
    assert view["should_poll"] is True


@pytest.mark.parametrize(
    ("status", "expected_class"),
    [
        ("PENDING", "is-pending"),
        ("PROCESSING", "is-processing"),
        ("SUCCEEDED", "is-succeeded"),
        ("FAILED", "is-failed"),
        ("UNKNOWN", "is-unknown"),
        (None, "is-unknown"),
    ],
)
def test_summary_task_status_maps_to_one_stable_css_class(
    status,
    expected_class,
):
    """Changing a task state must select exactly one semantic CSS class."""
    assert summary_ui_module.summary_task_state_class(status) == expected_class


def test_pending_task_is_shown_as_queued_active_work():
    view = summary_task_view_model(_task())

    assert view["task_id"] == "task-1"
    assert view["video_id"] == "BV1TEST"
    assert view["attempt_number"] == 1
    assert view["status"] == "PENDING"
    assert view["animation_state"] == "pending"
    assert view["status_label"] == "等待处理"
    assert view["stage_label"] == "任务已进入队列"
    assert view["can_retry"] is False
    assert view["should_poll"] is True
    assert view["result_sections"] == []
    assert view["error_message"] is None


def test_failed_task_can_retry_without_exposing_persisted_internal_text():
    task = _task(
        status="FAILED",
        stage="FAILED",
        error="RuntimeError: api_key=secret at C:/private/provider.py",
    )

    view = summary_task_view_model(task)

    assert view["animation_state"] == "failed"
    assert view["status_label"] == "总结失败"
    assert view["can_retry"] is True
    assert view["should_poll"] is False
    assert view["error_message"] == "视频总结未能完成，请重试。"
    assert "secret" not in str(view)
    assert "private" not in str(view)


def test_result_sections_tolerate_missing_or_malformed_optional_data():
    assert summary_result_sections({}) == []
    assert summary_result_sections(None) == []
    assert summary_result_sections({"summary": []}) == []


def test_result_renderer_allows_only_supported_video_metadata_fields():
    sections = summary_result_sections(
        {
            "video_info": {
                "title": "Safe title",
                "uploader": "Safe UP",
                "cookie": "session=secret",
                "api_key": "sk-secret",
                "raw_provider_response": "OpenAI 401 private",
            }
        }
    )

    assert sections == [
        {
            "key": "video_info",
            "title": "视频信息",
            "kind": "metadata",
            "content": {"title": "Safe title", "uploader": "Safe UP"},
        }
    ]
    assert "secret" not in str(sections).lower()
    assert "private" not in str(sections).lower()


def test_result_sections_preserve_all_supported_content_in_display_order():
    result = {
        "video_id": "BV1TEST",
        "video_info": {"title": "测试视频", "uploader": "测试作者"},
        "summary": {
            "one_line_summary": "一句话概括",
            "summary": "完整的详细总结。",
            "chapters": [{"start": "00:00", "title": "开场"}],
            "key_points": ["要点一", "要点二"],
            "keywords": ["测试", "总结"],
            "timestamps": [{"time": "00:10", "text": "关键时刻"}],
        },
    }

    sections = summary_result_sections(result)

    assert sections == [
        {
            "key": "video_info",
            "title": "视频信息",
            "kind": "metadata",
            "content": {
                "video_id": "BV1TEST",
                "title": "测试视频",
                "uploader": "测试作者",
            },
        },
        {
            "key": "one_line_summary",
            "title": "一句话总结",
            "kind": "text",
            "content": "一句话概括",
        },
        {
            "key": "detailed_summary",
            "title": "详细总结",
            "kind": "text",
            "content": "完整的详细总结。",
        },
        {
            "key": "chapters",
            "title": "章节",
            "kind": "items",
            "content": [{"start": "00:00", "title": "开场"}],
        },
        {
            "key": "key_points",
            "title": "关键要点",
            "kind": "items",
            "content": ["要点一", "要点二"],
        },
        {
            "key": "keywords",
            "title": "关键词",
            "kind": "items",
            "content": ["测试", "总结"],
        },
        {
            "key": "timestamps",
            "title": "时间点",
            "kind": "items",
            "content": [{"time": "00:10", "text": "关键时刻"}],
        },
    ]


def test_result_sections_accept_supported_top_level_fields():
    result = {
        "video_id": "BV1TEST",
        "video_url": "https://www.bilibili.com/video/BV1TEST",
        "title": "顶层标题",
        "one_line_summary": "顶层一句话",
        "detailed_summary": "顶层详细总结",
        "chapters": ["第一章"],
        "key_points": ["顶层要点"],
        "keywords": ["顶层关键词"],
        "timestamps": ["00:05"],
    }

    sections = summary_result_sections(result)

    assert [section["key"] for section in sections] == [
        "video_info",
        "one_line_summary",
        "detailed_summary",
        "chapters",
        "key_points",
        "keywords",
        "timestamps",
    ]
    assert sections[0]["content"] == {
        "video_id": "BV1TEST",
        "video_url": "https://www.bilibili.com/video/BV1TEST",
        "title": "顶层标题",
    }
    assert sections[1]["content"] == "顶层一句话"
    assert sections[2]["content"] == "顶层详细总结"


def test_result_sections_skip_malformed_optional_field_types():
    result = {
        "video_id": "BV1TEST",
        "summary": {
            "one_line_summary": ["not text"],
            "summary": {"not": "text"},
            "chapters": 3,
            "key_points": "not a list",
            "keywords": {"not": "a list"},
            "timestamps": "00:05",
        },
    }

    assert summary_result_sections(result) == [
        {
            "key": "video_info",
            "title": "视频信息",
            "kind": "metadata",
            "content": {"video_id": "BV1TEST"},
        }
    ]


def test_succeeded_task_stops_polling_and_exposes_structured_result():
    task = _task(
        status="SUCCEEDED",
        stage="COMPLETE",
        result={
            "video_id": "BV1TEST",
            "summary": {
                "summary": "完整总结",
                "key_points": ["要点"],
                "keywords": ["关键词"],
            },
        },
    )

    view = summary_task_view_model(task)

    assert view["animation_state"] == "success"
    assert view["status_label"] == "总结完成"
    assert view["stage_label"] == "视频总结已生成"
    assert view["can_retry"] is False
    assert view["should_poll"] is False
    assert [section["key"] for section in view["result_sections"]] == [
        "video_info",
        "detailed_summary",
        "key_points",
        "keywords",
    ]


def test_unknown_persisted_status_is_an_explicit_safe_terminal_fallback():
    view = summary_task_view_model(
        _task(status="PAUSED_BY_NEWER_VERSION", stage="PRIVATE_STAGE")
    )

    assert view["animation_state"] == "unknown"
    assert view["status_label"] == "任务状态未知"
    assert view["stage_label"] == "无法识别已保存的任务状态"
    assert view["can_retry"] is False
    assert view["should_poll"] is False
    assert "PRIVATE_STAGE" not in str(view)


def test_unknown_processing_stage_is_explicit_without_stopping_active_polling():
    view = summary_task_view_model(
        _task(status="PROCESSING", stage="NEW_PRIVATE_STAGE")
    )

    assert view["animation_state"] == "processing"
    assert view["status_label"] == "正在处理"
    assert view["stage_label"] == "正在处理（未知阶段）"
    assert view["should_poll"] is True
    assert "NEW_PRIVATE_STAGE" not in str(view)


@pytest.mark.parametrize(
    ("stage", "expected_label"),
    [
        ("METADATA", "正在获取视频信息"),
        ("TRANSCRIPT", "正在整理视频文字稿"),
        ("SPLIT", "正在划分总结片段"),
        ("SUMMARIZE_CHUNKS", "正在总结视频片段"),
        ("MERGE", "正在合并视频总结"),
    ],
)
def test_processing_stages_have_canonical_chinese_labels(stage, expected_label):
    view = summary_task_view_model(_task(status="PROCESSING", stage=stage))

    assert view["stage_label"] == expected_label


def test_submit_action_returns_the_task_created_by_the_service():
    expected_task = _task(task_id="submitted-task")

    class Service:
        def __init__(self):
            self.urls = []

        def submit(self, video_url):
            self.urls.append(video_url)
            return expected_task

    service = Service()

    action = submit_summary_action(
        service,
        "https://www.bilibili.com/video/BV1TEST",
    )

    assert action == {"task": expected_task, "error_message": None}
    assert service.urls == ["https://www.bilibili.com/video/BV1TEST"]


@pytest.mark.parametrize(
    ("error", "expected_message"),
    [
        (
            ValueError("input included api_key=secret"),
            "请输入包含 BV 号的有效 Bilibili 视频链接。",
        ),
        (
            RuntimeError("database path C:/private/summary.db"),
            "总结任务服务暂时不可用，请稍后重试。",
        ),
    ],
)
def test_submit_action_converts_expected_failures_to_safe_local_errors(
    error,
    expected_message,
):
    class Service:
        def submit(self, video_url):
            raise error

    action = submit_summary_action(Service(), "unsafe input")

    assert action == {"task": None, "error_message": expected_message}
    assert "secret" not in str(action)
    assert "private" not in str(action)


@pytest.mark.parametrize(
    "error",
    [
        ValueError("temporary read failure with api_key=secret"),
        RuntimeError("temporary database failure at C:/private/summary.db"),
    ],
)
def test_poll_summary_task_retries_after_safe_transient_read_error(error):
    class Service:
        def get(self, task_id):
            raise error

    outcome = poll_summary_task(Service(), "current-task")

    assert outcome == {
        "task": None,
        "error_message": "暂时无法读取当前任务，正在自动重试。",
        "should_poll": True,
    }
    assert "secret" not in str(outcome)
    assert "private" not in str(outcome)


def test_poll_summary_task_recovers_from_repository_operational_error(
    monkeypatch,
    tmp_path,
):
    def fail(*_args, **_kwargs):
        raise sqlite3.OperationalError("database locked at C:/private/db")

    monkeypatch.setattr(summary_service_module, "get_summary_task", fail)
    service = summary_service_module.SummaryTaskService(
        tmp_path / "summary.db",
        lambda video_url, progress_callback: None,
    )

    outcome = poll_summary_task(service, "current-task")

    assert outcome == {
        "task": None,
        "error_message": "暂时无法读取当前任务，正在自动重试。",
        "should_poll": True,
    }
    assert "private" not in str(outcome)


@pytest.mark.parametrize(
    ("status", "stage", "expected_should_poll"),
    [
        ("PENDING", None, True),
        ("PROCESSING", "MERGE", True),
        ("SUCCEEDED", "COMPLETE", False),
        ("FAILED", "FAILED", False),
    ],
)
def test_poll_summary_task_uses_the_safe_view_polling_policy(
    status,
    stage,
    expected_should_poll,
):
    task = _task(status=status, stage=stage)

    class Service:
        def get(self, task_id):
            return task

    assert poll_summary_task(Service(), task.task_id) == {
        "task": task,
        "error_message": None,
        "should_poll": expected_should_poll,
    }


def test_same_task_resubmission_clears_error_and_advances_selection_revision():
    task = _task(task_id="same-task")
    current = {
        "task_id": "same-task",
        "revision": 4,
        "error_message": "暂时无法读取当前任务，正在自动重试。",
    }

    updated = summary_selection_update(
        current,
        {"task": task, "error_message": None},
    )

    assert updated == {
        "task_id": "same-task",
        "revision": 5,
        "error_message": None,
    }


def test_retry_action_returns_the_linked_task_created_by_the_service():
    expected_task = _task(task_id="retry-task", retry_of="failed-task")

    class Service:
        def __init__(self):
            self.task_ids = []

        def retry(self, task_id):
            self.task_ids.append(task_id)
            return expected_task

    service = Service()

    action = retry_summary_action(service, "failed-task")

    assert action == {"task": expected_task, "error_message": None}
    assert service.task_ids == ["failed-task"]


@pytest.mark.parametrize(
    ("error", "expected_message"),
    [
        (
            ValueError("task details included api_key=secret"),
            "该任务当前无法重试，请刷新后再试。",
        ),
        (
            RuntimeError("database path C:/private/summary.db"),
            "暂时无法重试，请稍后再试。",
        ),
    ],
)
def test_retry_action_converts_expected_failures_to_safe_local_errors(
    error,
    expected_message,
):
    class Service:
        def retry(self, task_id):
            raise error

    action = retry_summary_action(Service(), "failed-task")

    assert action == {"task": None, "error_message": expected_message}
    assert "secret" not in str(action)
    assert "private" not in str(action)


def test_history_renderer_exposes_selectable_tasks_and_designed_metadata():
    created_at = datetime(2026, 8, 13, 12, 30, tzinfo=UTC)
    finished_at = datetime(2026, 8, 13, 12, 35, tzinfo=UTC)
    succeeded = summary_task_view_model(
        _task(
            task_id="succeeded-task",
            status="SUCCEEDED",
            stage="COMPLETE",
            result={"video_info": {"title": "A <safe> title"}},
            created_at=created_at,
            updated_at=finished_at,
            finished_at=finished_at,
        )
    )
    failed = summary_task_view_model(
        _task(
            task_id="failed-task",
            status="FAILED",
            stage="FAILED",
            attempt_number=2,
            created_at=created_at,
            updated_at=finished_at,
        )
    )

    choices = summary_ui_module.summary_history_choices([succeeded, failed])
    markup = str(
        summary_ui_module.render_summary_history(
            [succeeded, failed],
            selected_task_id="failed-task",
        )
    )

    assert list(choices) == ["succeeded-task", "failed-task"]
    assert "A <safe> title" in choices["succeeded-task"]
    assert "结果可复用" in choices["succeeded-task"]
    assert "2026-08-13 12:30 UTC" in choices["succeeded-task"]
    assert "2026-08-13 12:35 UTC" in choices["succeeded-task"]
    assert "第 2 次" in choices["failed-task"]
    assert "未完成" in choices["failed-task"]
    assert "结果可复用" not in choices["failed-task"]
    assert 'id="summary_history_selection"' in markup
    assert 'value="succeeded-task"' in markup
    assert 'value="failed-task" checked="checked"' in markup
    assert "A &lt;safe&gt; title" in markup


def test_fresh_history_selection_reopens_failed_task_for_retry():
    failed = _task(task_id="persisted-failed", status="FAILED", stage="FAILED")
    retried = _task(
        task_id="retry-task",
        retry_of="persisted-failed",
        attempt_number=2,
    )

    class Service:
        def __init__(self):
            self.retried_ids = []

        def get(self, task_id):
            return failed if task_id == failed.task_id else None

        def retry(self, task_id):
            self.retried_ids.append(task_id)
            return retried

    service = Service()

    selected = summary_server_module.select_summary_history_action(
        service,
        "persisted-failed",
    )
    current = summary_selection_update(
        {"task_id": None, "revision": 0, "error_message": None},
        selected,
    )
    retried_action = retry_summary_action(service, current["task_id"])

    assert selected == {"task": failed, "error_message": None}
    assert current["task_id"] == "persisted-failed"
    assert summary_task_view_model(selected["task"])["can_retry"] is True
    assert retried_action == {"task": retried, "error_message": None}
    assert service.retried_ids == ["persisted-failed"]


def test_summary_ui_contains_the_complete_workflow_regions():
    markup = str(build_summary_ui())

    assert 'id="summary_video_url"' in markup
    assert 'id="summary_submit"' in markup
    assert 'id="summary_task_card"' in markup
    assert 'id="summary_task_state"' in markup
    assert 'id="summary_result_sections"' in markup
    assert 'id="summary_retry_action"' in markup
    assert 'id="summary_history"' in markup
    assert "当前任务" in markup
    assert "历史任务" in markup


def test_summary_ui_keeps_form_task_results_and_history_in_distinct_panels():
    """Collapsing workflow regions would erase the task-oriented hierarchy."""
    markup = str(build_summary_ui())

    assert 'class="summary-form dashboard-panel"' in markup
    assert 'class="summary-current dashboard-panel"' in markup
    assert 'class="summary-results dashboard-panel"' in markup
    assert 'class="summary-history dashboard-panel"' in markup


def test_create_app_owns_one_started_summary_service_for_all_sessions(
    monkeypatch,
    tmp_path,
):
    services = []
    summary_registrations = []

    class Service:
        def __init__(self, database_path, runner, max_workers=2):
            self.database_path = database_path
            self.runner = runner
            self.max_workers = max_workers
            self.start_calls = 0
            self.shutdown_calls = 0
            services.append(self)

        def start(self):
            self.start_calls += 1

        def shutdown(self):
            self.shutdown_calls += 1

    class FakeApp:
        def __init__(self, app_ui, server, static_assets=None):
            self.app_ui = app_ui
            self.server = server
            self.static_assets = static_assets
            self.shutdown_callbacks = []

        def on_shutdown(self, callback):
            self.shutdown_callbacks.append(callback)
            return callback

    def runner(*_args, **_kwargs):
        return None

    monkeypatch.setattr(app_module, "App", FakeApp)
    monkeypatch.setattr(app_module, "SummaryTaskService", Service, raising=False)
    monkeypatch.setattr(
        app_module,
        "register_ranking_server",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        app_module,
        "register_summary_server",
        lambda _input, _output, _session, service: summary_registrations.append(
            service
        ),
        raising=False,
    )

    app = app_module.create_app(
        database_path=tmp_path / "app.db",
        summary_runner=runner,
    )

    assert len(services) == 1
    assert services[0].runner is runner
    assert services[0].start_calls == 1
    assert len(app.shutdown_callbacks) == 1
    assert app.static_assets == app_module.STATIC_ASSETS

    app.server(object(), object(), object())
    app.server(object(), object(), object())

    assert summary_registrations == [services[0], services[0]]
    assert len(services) == 1

    app.shutdown_callbacks[0]()
    assert services[0].shutdown_calls == 1


def test_two_app_factories_cannot_execute_one_database_concurrently(
    monkeypatch,
    tmp_path,
):
    registrations = []
    first_entered = Event()
    release_first = Event()
    second_entered = Event()

    class FakeApp:
        def __init__(self, app_ui, server, static_assets=None):
            self.server = server
            self.static_assets = static_assets
            self.shutdown_callbacks = []

        def on_shutdown(self, callback):
            self.shutdown_callbacks.append(callback)
            return callback

    def first_runner(video_url, progress_callback):
        first_entered.set()
        assert release_first.wait(timeout=5)
        return {"video_id": "BV1TEST"}

    def second_runner(video_url, progress_callback):
        second_entered.set()
        return {"video_id": "BV1TEST"}

    monkeypatch.setattr(app_module, "App", FakeApp)
    monkeypatch.setattr(
        app_module,
        "register_ranking_server",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        app_module,
        "register_summary_server",
        lambda _input, _output, _session, service: registrations.append(service),
    )

    first_app = app_module.create_app(
        database_path=tmp_path / "shared.db",
        summary_runner=first_runner,
    )
    first_app.server(object(), object(), object())
    registrations[0].submit("https://www.bilibili.com/video/BV1TEST")
    assert first_entered.wait(timeout=5)

    try:
        with pytest.raises(RuntimeError, match="already owned"):
            app_module.create_app(
                database_path=tmp_path / "shared.db",
                summary_runner=second_runner,
            )
        assert not second_entered.is_set()
    finally:
        release_first.set()
        first_app.shutdown_callbacks[0]()


def test_importing_web_app_does_not_load_dotenv_through_default_runner():
    environment = os.environ.copy()
    environment.pop("SUMMARY_VIDEO_API_KEY", None)
    script = (
        "import os; "
        "os.environ.pop('SUMMARY_VIDEO_API_KEY', None); "
        "import web_app.app; "
        "raise SystemExit(1 if 'SUMMARY_VIDEO_API_KEY' in os.environ else 0)"
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert completed.returncode == 0
