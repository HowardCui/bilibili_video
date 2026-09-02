import json
import time
from datetime import UTC, datetime
from io import StringIO
from types import SimpleNamespace

import pytest

from app_logging import configure_logging, shutdown_logging
from ranking_collector import client as ranking_client
from ranking_collector.repository import initialize_database
from ranking_collector.service import collect_once
from uploader_analysis import client as uploader_client
from uploader_analysis.repository import (
    get_uploader_detail,
    initialize_uploader_database,
)
from uploader_analysis.service import collect_uploader_history
from video_processing.get_danmaku import download_danmaku
from web_app import app as web_app_module
from web_app.summary.service import SummaryTaskService


def _events(path):
    return [
        json.loads(line)["event"]
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


def _ranking_video():
    return {
        "bvid": "BV1LOG",
        "title": "日志测试视频",
        "owner": {"mid": 123, "name": "日志 UP"},
        "pubdate": 1_700_000_000,
        "duration": 60,
        "stat": {
            "view": 100,
            "like": 10,
            "coin": 2,
            "favorite": 3,
            "reply": 4,
            "danmaku": 5,
            "share": 1,
        },
    }


def test_ranking_collection_logs_run_and_partition_results(tmp_path):
    log_dir = tmp_path / "logs"
    configure_logging("ranking", log_dir=log_dir, stream=StringIO())
    result = collect_once(
        collected_at=datetime(2026, 9, 1, tzinfo=UTC),
        fetch_function=lambda **_kwargs: [_ranking_video()],
        database_path=tmp_path / "ranking.db",
        partitions={"all": {"name": "全站", "rid": 0, "enabled": True}},
        limit=1,
    )
    shutdown_logging()

    assert result["succeeded"] is True
    assert _events(log_dir / "ranking.log") == [
        "ranking_collection_started",
        "ranking_partition_succeeded",
        "ranking_collection_finished",
    ]


def test_uploader_batch_limit_logs_paused_state(tmp_path):
    database_path = tmp_path / "ranking.db"
    initialize_database(database_path)
    initialize_uploader_database(database_path)
    now = datetime(2026, 9, 1, tzinfo=UTC)
    from ranking_collector.repository import connect_database

    with connect_database(database_path) as connection:
        connection.execute(
            """INSERT INTO uploader_profiles (
                uploader_id,current_name,first_ranked_at,last_ranked_at,updated_at
            ) VALUES (123,'日志 UP',?,?,?)""",
            (now.isoformat(), now.isoformat(), now.isoformat()),
        )

    def fetch_page(_uploader_id, page):
        video = _ranking_video()
        video["bvid"] = f"BV{page}LOG"
        return {
            "videos": [video],
            "next_cursor": page + 1,
            "has_more": True,
        }

    log_dir = tmp_path / "logs"
    configure_logging("web", log_dir=log_dir, stream=StringIO())
    result = collect_uploader_history(
        123,
        database_path,
        fetch_page=fetch_page,
        sleep=lambda _seconds: None,
        now=lambda: now,
        max_pages=1,
    )
    shutdown_logging()

    assert result["status"] == "PAUSED"
    assert get_uploader_detail(123, database_path)["task"]["status"] == "PAUSED"
    assert _events(log_dir / "web.log") == [
        "uploader_collection_started",
        "uploader_page_saved",
        "uploader_collection_paused",
    ]


def test_summary_task_logs_reported_stage_and_completion(tmp_path):
    def runner(_url, progress_callback):
        progress_callback("SUBTITLE")
        return {"video_id": "BV1TEST", "summary": {"text": "done"}}

    log_dir = tmp_path / "logs"
    configure_logging("web", log_dir=log_dir, stream=StringIO())
    service = SummaryTaskService(tmp_path / "summary.db", runner, max_workers=1)
    service.start()
    try:
        task = service.submit("https://www.bilibili.com/video/BV1TEST")
        deadline = time.monotonic() + 5
        while service.get(task.task_id).status != "SUCCEEDED":
            assert time.monotonic() < deadline
            time.sleep(0.01)
    finally:
        service.shutdown()
        shutdown_logging()

    events = _events(log_dir / "web.log")
    assert "summary_task_started" in events
    assert "summary_stage_changed" in events
    assert "summary_task_succeeded" in events


def test_danmaku_cache_hit_is_logged_without_content(tmp_path):
    cached = tmp_path / "BV1CACHE.danmaku.xml"
    cached.write_text("<i><d>不应写入日志的弹幕正文</d></i>", encoding="utf-8")
    log_dir = tmp_path / "logs"
    configure_logging("web", log_dir=log_dir, stream=StringIO())

    result = download_danmaku(
        "https://www.bilibili.com/video/BV1CACHE",
        raw_info={"id": "BV1CACHE"},
        output_dir=tmp_path,
    )
    shutdown_logging()

    lines = (log_dir / "web.log").read_text(encoding="utf-8")
    assert result == cached
    assert '"event": "danmaku_cache_hit"' in lines
    assert "不应写入日志的弹幕正文" not in lines


def test_ranking_cookie_fallback_is_logged_without_cookie_data(tmp_path, monkeypatch):
    anonymous = object()
    cookie = object()

    def fetch(session, _rid, _limit, _timeout):
        if session is anonymous:
            raise ranking_client.RankingClientError("Cookie: SESSDATA=secret")
        return [_ranking_video()]

    monkeypatch.setattr(ranking_client, "get_shared_session", lambda: anonymous)
    monkeypatch.setattr(ranking_client, "get_cookie_session", lambda: cookie)
    monkeypatch.setattr(ranking_client, "_fetch_ranking_with_session", fetch)
    log_dir = tmp_path / "logs"
    configure_logging("ranking", log_dir=log_dir, stream=StringIO())

    result = ranking_client.fetch_ranking(0, limit=1)
    shutdown_logging()

    lines = (log_dir / "ranking.log").read_text(encoding="utf-8")
    assert len(result) == 1
    assert '"event": "ranking_cookie_fallback"' in lines
    assert "secret" not in lines


def test_uploader_cookie_fallback_is_logged_without_request_material(
    tmp_path, monkeypatch
):
    anonymous = object()
    cookie = object()

    def fetch(session, *_args, **_kwargs):
        if session is anonymous:
            raise uploader_client.UploaderClientError(
                "w_rid=signature", "ANONYMOUS_RISK_CONTROL"
            )
        return {"videos": [], "next_cursor": 2, "has_more": False, "total": 0}

    monkeypatch.setattr(uploader_client, "get_shared_session", lambda: anonymous)
    monkeypatch.setattr(uploader_client, "get_cookie_session", lambda: cookie)
    monkeypatch.setattr(uploader_client, "_fetch_with_session", fetch)
    log_dir = tmp_path / "logs"
    configure_logging("web", log_dir=log_dir, stream=StringIO())

    result = uploader_client.fetch_uploader_page(123)
    shutdown_logging()

    lines = (log_dir / "web.log").read_text(encoding="utf-8")
    assert result["has_more"] is False
    assert '"event": "uploader_cookie_fallback"' in lines
    assert "signature" not in lines


def test_web_main_logs_unhandled_startup_failure(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"

    def configure(_component):
        return configure_logging("web", log_dir=log_dir, stream=StringIO())

    def create_failed_app():
        def fail_run(**_kwargs):
            raise RuntimeError("Cookie: SESSDATA=secret")

        return SimpleNamespace(run=fail_run)

    monkeypatch.setattr(web_app_module, "configure_logging", configure)
    monkeypatch.setattr(web_app_module, "create_app", create_failed_app)

    with pytest.raises(RuntimeError):
        web_app_module.main()
    shutdown_logging()

    lines = (log_dir / "web.log").read_text(encoding="utf-8")
    assert '"event": "web_failed"' in lines
    assert "secret" not in lines
