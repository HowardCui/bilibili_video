"""UP 历史投稿采集编排与分析。"""

import random
import time
from datetime import UTC, datetime
from statistics import mean, median
from threading import Lock, Thread

from uploader_analysis.client import UploaderClientError, fetch_uploader_page
from uploader_analysis.repository import (
    create_collection_task,
    fail_collection_task,
    get_uploader_detail,
    save_uploader_page,
)

METRIC_KEYS = (
    "views",
    "likes",
    "coins",
    "favorites",
    "comments",
    "danmaku",
    "shares",
)


def calculate_uploader_analysis(videos, ranked_bvids):
    ordered = sorted(videos, key=lambda item: item["published_at"])
    views = [int(item.get("views") or 0) for item in ordered]
    intervals = []
    for previous, current in zip(ordered, ordered[1:], strict=False):
        earlier = datetime.fromisoformat(previous["published_at"])
        later = datetime.fromisoformat(current["published_at"])
        intervals.append((later - earlier).total_seconds() / 86400)
    threshold = None
    viral_bvids = []
    if len(views) >= 4:
        sorted_views = sorted(views)
        threshold = sorted_views[max(0, int(len(sorted_views) * 0.75) - 1)]
        viral_bvids = [
            item["bvid"] for item in ordered if int(item.get("views") or 0) > threshold
        ]
    ranked_views = [
        int(item.get("views") or 0) for item in ordered if item["bvid"] in ranked_bvids
    ]
    normal_views = [
        int(item.get("views") or 0)
        for item in ordered
        if item["bvid"] not in ranked_bvids
    ]
    metric_summary = {}
    for key in METRIC_KEYS:
        values = [int(item.get(key) or 0) for item in ordered]
        metric_summary[key] = {
            "average": mean(values) if values else None,
            "median": median(values) if values else None,
        }
    monthly_counts = {}
    for item in ordered:
        month = item["published_at"][:7]
        monthly_counts[month] = monthly_counts.get(month, 0) + 1
    return {
        "video_count": len(ordered),
        "average_views": mean(views) if views else None,
        "median_views": median(views) if views else None,
        "average_publish_interval_days": mean(intervals) if intervals else None,
        "viral_threshold": threshold,
        "viral_bvids": viral_bvids,
        "viral_ratio": len(viral_bvids) / len(ordered) if viral_bvids else 0,
        "ranked_average_views": mean(ranked_views) if ranked_views else None,
        "normal_average_views": mean(normal_views) if normal_views else None,
        "metric_summary": metric_summary,
        "monthly_publish_counts": monthly_counts,
    }


def calculate_uploader_ranking_analysis(videos, ranking_entries):
    if not ranking_entries:
        return {
            "ranked_video_count": 0,
            "ranking_appearance_count": 0,
            "first_ranked_at": None,
            "last_ranked_at": None,
            "best_rank": None,
            "average_publish_to_rank_days": None,
        }
    ordered = sorted(ranking_entries, key=lambda item: item["collected_at"])
    first_by_bvid = {}
    for entry in ordered:
        first_by_bvid.setdefault(entry["bvid"], entry)
    published = {item["bvid"]: item["published_at"] for item in videos}
    delays = []
    for bvid, entry in first_by_bvid.items():
        if bvid in published:
            ranked_at = datetime.fromisoformat(entry["collected_at"])
            published_at = datetime.fromisoformat(published[bvid])
            delays.append(max(0, (ranked_at - published_at).total_seconds() / 86400))
    return {
        "ranked_video_count": len(first_by_bvid),
        "ranking_appearance_count": len(ordered),
        "first_ranked_at": ordered[0]["collected_at"],
        "last_ranked_at": ordered[-1]["collected_at"],
        "best_rank": min(int(item["rank"]) for item in ordered),
        "average_publish_to_rank_days": mean(delays) if delays else None,
    }


def collect_uploader_history(
    uploader_id,
    database_path,
    fetch_page=fetch_uploader_page,
    sleep=time.sleep,
    now=None,
    max_pages=20,
):
    current_time = now or (lambda: datetime.now(UTC))
    task_id = create_collection_task(uploader_id, database_path, now=current_time())
    detail = get_uploader_detail(uploader_id, database_path)
    cursor = int(detail["task"]["cursor"])
    pages_saved = 0
    try:
        while pages_saved < max_pages:
            page = None
            for attempt in range(3):
                try:
                    page = fetch_page(uploader_id, cursor)
                    break
                except UploaderClientError as error:
                    risk_control = error.error_code.endswith("RISK_CONTROL")
                    transient = error.error_code in {
                        "REQUEST_FAILED",
                        "API_ERROR",
                    }
                    if not transient and not risk_control:
                        raise
                    if attempt == 2:
                        raise
                    if risk_control:
                        sleep(random.uniform(30, 60))
                    else:
                        sleep(2**attempt)
            collected_at = current_time()
            save_uploader_page(
                task_id,
                uploader_id,
                page["videos"],
                page["next_cursor"],
                page["has_more"],
                collected_at,
                database_path,
            )
            pages_saved += 1
            if not page["has_more"]:
                return {
                    "task_id": task_id,
                    "status": "SUCCEEDED",
                    "pages_saved": pages_saved,
                }
            cursor = page["next_cursor"]
            sleep(random.uniform(1.0, 2.0))
        return {"task_id": task_id, "status": "RUNNING", "pages_saved": pages_saved}
    except UploaderClientError as error:
        fail_collection_task(task_id, error.error_code, database_path)
        return {"task_id": task_id, "status": "FAILED", "error_code": error.error_code}
    except Exception:
        fail_collection_task(task_id, "UNEXPECTED", database_path)
        return {"task_id": task_id, "status": "FAILED", "error_code": "UNEXPECTED"}


class UploaderCollectionService:
    def __init__(self, database_path, collector=collect_uploader_history):
        self.database_path = database_path
        self.collector = collector
        self.lock = Lock()
        self.threads = {}

    def start(self, uploader_id):
        with self.lock:
            thread = self.threads.get(uploader_id)
            if thread is not None and thread.is_alive():
                return False
            thread = Thread(target=self._run, args=(uploader_id,), daemon=True)
            self.threads[uploader_id] = thread
            thread.start()
            return True

    def _run(self, uploader_id):
        self.collector(uploader_id, self.database_path)

    def shutdown(self, timeout=10):
        with self.lock:
            threads = list(self.threads.values())
        for thread in threads:
            thread.join(timeout=timeout)


__all__ = [
    "UploaderCollectionService",
    "calculate_uploader_analysis",
    "calculate_uploader_ranking_analysis",
    "collect_uploader_history",
]
