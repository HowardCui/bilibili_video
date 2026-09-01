from datetime import UTC, datetime
from pathlib import Path

from htmltools import Tag

from ranking_collector.repository import connect_database, initialize_database
from uploader_analysis.repository import initialize_uploader_database
from web_app.layout import build_app_ui
from web_app.uploader.queries import build_uploader_page_data
from web_app.uploader.ui import build_uploader_ui, uploader_view_model

_CSS = Path(__file__).parents[1] / "web_app" / "www" / "layout.css"


def test_app_has_independent_uploader_page_with_existing_visual_contract():
    markup = str(build_app_ui())
    uploader_markup = str(build_uploader_ui())
    css = _CSS.read_text(encoding="utf-8")
    assert "UP 分析" in markup
    assert 'data-value="UP 分析"' in markup
    assert "dashboard-panel" in uploader_markup
    assert "section-kicker" in uploader_markup
    assert "uploader-dashboard" in css
    assert "var(--space-" in css
    assert "rgba(" not in css
    assert build_uploader_ui.__annotations__["return"] is Tag


def test_uploader_page_distinguishes_selection_and_history_states(tmp_path):
    database_path = tmp_path / "ranking.db"
    now = datetime(2026, 8, 28, tzinfo=UTC).isoformat()
    initialize_database(database_path)
    initialize_uploader_database(database_path)
    with connect_database(database_path) as connection:
        connection.execute(
            """INSERT INTO uploader_profiles (
                uploader_id,current_name,first_ranked_at,last_ranked_at,updated_at
            ) VALUES (123,'示例 UP',?,?,?)""",
            (now, now, now),
        )
    assert build_uploader_page_data(None, database_path)["status"] == "NO_SELECTION"
    page = build_uploader_page_data(123, database_path)
    assert page["status"] == "NO_HISTORY"
    assert page["profile"]["current_name"] == "示例 UP"


def test_uploader_view_maps_safe_status_and_sample_copy():
    view = uploader_view_model(
        {
            "status": "READY",
            "profile": {"current_name": "示例 UP", "uploader_id": 123},
            "task": {"status": "FAILED", "error_code": "RISK_CONTROL"},
            "videos": [{"bvid": "BV1", "title": "历史视频", "views": 100}],
            "analysis": {
                "video_count": 1,
                "average_views": 100,
                "median_views": 100,
                "average_publish_interval_days": None,
                "viral_ratio": 0,
                "ranked_average_views": 100,
                "normal_average_views": None,
                "viral_bvids": [],
            },
        }
    )
    assert view["title"] == "示例 UP"
    assert "风控" in view["collection_message"]
    assert "1 个历史投稿样本" in view["sample_message"]


def test_uploader_view_marks_paused_batch_as_continuable():
    view = uploader_view_model(
        {
            "status": "READY",
            "profile": {"current_name": "示例 UP", "uploader_id": 123},
            "task": {"status": "PAUSED"},
            "videos": [],
            "analysis": {"video_count": 600},
        }
    )

    assert "可继续采集" in view["collection_message"]
