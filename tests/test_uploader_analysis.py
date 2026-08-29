import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

from curl_cffi.requests.exceptions import HTTPError

from ranking_collector.models import ranking_item_from_bilibili
from ranking_collector.repository import (
    connect_database,
    create_collection_run,
    get_latest_successful_snapshot,
    initialize_database,
    save_snapshot,
)
from ranking_collector.service import build_snapshot
from uploader_analysis.client import (
    UploaderClientError,
    build_wbi_params,
    fetch_uploader_page,
)
from uploader_analysis.repository import (
    create_collection_task,
    get_uploader_detail,
    initialize_uploader_database,
    list_ranked_uploaders,
    save_uploader_page,
    sync_ranked_uploaders,
)
from uploader_analysis.service import (
    calculate_uploader_analysis,
    calculate_uploader_ranking_analysis,
    collect_uploader_history,
)


def _raw_video(bvid="BV1UP", mid=123, views=1000, pubdate=1_700_000_000):
    return {
        "bvid": bvid,
        "title": f"video-{bvid}",
        "owner": {"mid": mid, "name": "示例 UP"},
        "pubdate": pubdate,
        "stat": {
            "view": views,
            "like": 100,
            "coin": 20,
            "favorite": 30,
            "reply": 10,
            "danmaku": 5,
            "share": 2,
        },
    }


def _seed_profile(database_path, now):
    initialize_database(database_path)
    initialize_uploader_database(database_path)
    with connect_database(database_path) as connection:
        connection.execute(
            """INSERT INTO uploader_profiles (
                uploader_id,current_name,first_ranked_at,last_ranked_at,updated_at
            ) VALUES (123,'示例 UP',?,?,?)""",
            (now.isoformat(), now.isoformat(), now.isoformat()),
        )


def test_ranking_item_reads_and_persists_numeric_uploader_id(tmp_path):
    database_path = tmp_path / "ranking.db"
    collected_at = datetime(2026, 8, 28, tzinfo=UTC)
    item = ranking_item_from_bilibili(_raw_video(), "全站", 1, collected_at)
    assert item.video.uploader_id == 123
    initialize_database(database_path)
    run_id = create_collection_run(collected_at, database_path)
    save_snapshot(
        run_id,
        build_snapshot([_raw_video()], "全站", collected_at),
        database_path,
    )
    loaded = get_latest_successful_snapshot("全站", database_path=database_path)
    assert loaded.items[0].video.uploader_id == 123


def test_initialize_database_adds_nullable_uid_to_legacy_items(tmp_path):
    database_path = tmp_path / "legacy.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE ranking_items ("
            "id INTEGER PRIMARY KEY,bvid TEXT,collected_at TEXT)"
        )
    initialize_database(database_path)
    with connect_database(database_path) as connection:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(ranking_items)")
        }
    assert "uploader_id" in columns


def test_ranked_uploader_profile_requires_confirmed_uid(tmp_path):
    database_path = tmp_path / "ranking.db"
    collected_at = datetime(2026, 8, 28, tzinfo=UTC)
    initialize_database(database_path)
    run_id = create_collection_run(collected_at, database_path)
    save_snapshot(
        run_id,
        build_snapshot([_raw_video()], "全站", collected_at),
        database_path,
    )
    assert sync_ranked_uploaders(database_path) == 1
    uploader = list_ranked_uploaders(database_path)[0]
    assert uploader["uploader_id"] == 123
    assert uploader["current_name"] == "示例 UP"


def test_task_is_unique_and_page_save_advances_cursor(tmp_path):
    database_path = tmp_path / "ranking.db"
    now = datetime(2026, 8, 28, tzinfo=UTC)
    _seed_profile(database_path, now)
    task_id = create_collection_task(123, database_path, now=now)
    assert create_collection_task(123, database_path, now=now) == task_id
    save_uploader_page(
        task_id,
        123,
        [_raw_video(), _raw_video("BV2UP", views=5000)],
        2,
        True,
        now,
        database_path,
    )
    detail = get_uploader_detail(123, database_path)
    assert detail["task"]["cursor"] == 2
    assert len(detail["videos"]) == 2


def test_self_history_and_ranking_analysis_are_sample_aware():
    videos = [
        {"bvid": "A", "views": 100, "published_at": "2026-01-01T00:00:00+00:00"},
        {"bvid": "B", "views": 200, "published_at": "2026-01-11T00:00:00+00:00"},
        {"bvid": "C", "views": 1000, "published_at": "2026-01-21T00:00:00+00:00"},
        {"bvid": "D", "views": 2000, "published_at": "2026-01-31T00:00:00+00:00"},
    ]
    analysis = calculate_uploader_analysis(videos, {"C", "D"})
    assert analysis["median_views"] == 600.0
    assert analysis["viral_bvids"] == ["D"]
    assert analysis["monthly_publish_counts"] == {"2026-01": 4}
    ranking = calculate_uploader_ranking_analysis(
        videos,
        [
            {"bvid": "A", "rank": 8, "collected_at": "2026-01-03T00:00:00+00:00"},
            {"bvid": "A", "rank": 3, "collected_at": "2026-01-04T00:00:00+00:00"},
        ],
    )
    assert ranking["ranking_appearance_count"] == 2
    assert ranking["best_rank"] == 3
    assert ranking["average_publish_to_rank_days"] == 2.0


def test_wbi_signing_and_fixed_page_response():
    signed = build_wbi_params(
        {"mid": 123, "keyword": "a!b(c)*d"},
        "7cd084941338484aae1ad9425b84077c",
        "4932caff0ff746eab6f01bf08b70ac45",
        1_700_000_000,
    )
    assert signed["keyword"] == "abcd"
    assert len(signed["w_rid"]) == 32
    calls = []
    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {
            "code": 0,
            "data": {
                "list": {"vlist": [_raw_video()]},
                "page": {"count": 1, "ps": 30, "pn": 1},
            },
        },
    )

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return response

    page = fetch_uploader_page(
        123,
        session=SimpleNamespace(get=get),
        wbi_keys=("a" * 32, "b" * 32),
        timestamp=1_700_000_000,
    )
    assert page["videos"][0]["bvid"] == "BV1UP"
    assert page["has_more"] is False
    assert "w_rid=" in calls[0][0]


def test_collection_retries_transient_errors_and_resumes_pages(tmp_path):
    database_path = tmp_path / "ranking.db"
    now = datetime(2026, 8, 28, tzinfo=UTC)
    _seed_profile(database_path, now)
    attempts = []
    waits = []

    def fetch_page(_uploader_id, page):
        attempts.append(page)
        if len(attempts) < 3:
            raise UploaderClientError("temporary", "REQUEST_FAILED")
        return {
            "videos": [_raw_video(f"BV{page}UP")],
            "next_cursor": page + 1,
            "has_more": page == 1,
        }

    result = collect_uploader_history(
        123,
        database_path,
        fetch_page=fetch_page,
        sleep=waits.append,
        now=lambda: now,
    )
    assert result["status"] == "SUCCEEDED"
    assert attempts == [1, 1, 1, 2]
    assert waits[:2] == [1, 2]


def test_http_412_is_reported_as_risk_control():
    response = SimpleNamespace(status_code=412)

    def get(_url, **_kwargs):
        return SimpleNamespace(
            raise_for_status=lambda: (_ for _ in ()).throw(
                HTTPError("HTTP Error 412", response=response)
            )
        )

    try:
        fetch_uploader_page(
            123,
            session=SimpleNamespace(get=get),
            wbi_keys=("a" * 32, "b" * 32),
        )
    except UploaderClientError as error:
        assert error.error_code == "RISK_CONTROL"
        assert error.http_status == 412
    else:
        raise AssertionError("HTTP 412 should raise UploaderClientError")


def test_cookie_risk_control_uses_long_backoff(tmp_path, monkeypatch):
    database_path = tmp_path / "ranking.db"
    now = datetime(2026, 8, 28, tzinfo=UTC)
    _seed_profile(database_path, now)
    attempts = []
    waits = []

    def fetch_page(_uploader_id, _page):
        attempts.append(1)
        if len(attempts) < 3:
            raise UploaderClientError("blocked", "COOKIE_RISK_CONTROL")
        return {"videos": [], "next_cursor": 2, "has_more": False}

    monkeypatch.setattr("uploader_analysis.service.random.uniform", lambda _a, _b: 30)
    result = collect_uploader_history(
        123,
        database_path,
        fetch_page=fetch_page,
        sleep=waits.append,
        now=lambda: now,
    )
    assert result["status"] == "SUCCEEDED"
    assert attempts == [1, 1, 1]
    assert waits == [30, 30]
