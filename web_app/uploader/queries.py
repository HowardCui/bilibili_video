"""UP 分析页面的安全查询转换。"""

from uploader_analysis.repository import (
    get_uploader_detail,
    list_ranked_uploaders,
    sync_ranked_uploaders,
)
from uploader_analysis.service import (
    calculate_uploader_analysis,
    calculate_uploader_ranking_analysis,
)

from .visualization import build_uploader_visualization


def list_uploader_choices(database_path):
    sync_ranked_uploaders(database_path)
    return {
        str(item["uploader_id"]): (
            f"{item['current_name']}（UID {item['uploader_id']}）"
        )
        for item in list_ranked_uploaders(database_path)
    }


def _visualization(videos, ranked_bvids, analysis, metric):
    try:
        return build_uploader_visualization(
            videos, ranked_bvids, analysis, metric=metric
        )
    except (KeyError, TypeError, ValueError):
        return {
            "status": "QUERY_FAILED",
            "metric": metric,
            "sample_count": len(videos),
        }


def build_uploader_page_data(uploader_id, database_path, metric="views"):
    if uploader_id in (None, ""):
        analysis = calculate_uploader_analysis([], set())
        return {
            "status": "NO_SELECTION",
            "profile": None,
            "task": None,
            "videos": [],
            "analysis": analysis,
            "visualization": _visualization([], set(), analysis, metric),
        }
    detail = get_uploader_detail(int(uploader_id), database_path)
    if detail is None:
        analysis = calculate_uploader_analysis([], set())
        return {
            "status": "UNCONFIRMED",
            "profile": None,
            "task": None,
            "videos": [],
            "analysis": analysis,
            "visualization": _visualization([], set(), analysis, metric),
        }
    analysis = calculate_uploader_analysis(detail["videos"], detail["ranked_bvids"])
    analysis.update(
        calculate_uploader_ranking_analysis(detail["videos"], detail["rankings"])
    )
    return {
        "status": "READY" if detail["videos"] else "NO_HISTORY",
        "profile": detail["profile"],
        "task": detail["task"],
        "videos": detail["videos"],
        "analysis": analysis,
        "visualization": _visualization(
            detail["videos"], detail["ranked_bvids"], analysis, metric
        ),
    }


__all__ = ["build_uploader_page_data", "list_uploader_choices"]
