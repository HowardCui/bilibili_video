"""UP 历史投稿可视化所需的只读数据转换。"""

from collections import Counter
from datetime import datetime
from statistics import fmean, median

METRIC_LABELS = {
    "views": "播放",
    "likes": "点赞",
    "coins": "投币",
    "favorites": "收藏",
    "comments": "评论",
    "danmaku": "弹幕",
    "shares": "分享",
}

MAX_SERIES_POINTS = 240


def _number(value):
    return max(0, int(value or 0))


def _ordered_videos(videos):
    return sorted(
        (dict(video) for video in videos),
        key=lambda video: (video.get("published_at") or "", video.get("bvid") or ""),
    )


def _sample_points(points, limit):
    if len(points) <= limit:
        return points
    if limit == 1:
        return [points[-1]]
    indexes = {
        round(index * (len(points) - 1) / (limit - 1)) for index in range(limit)
    }
    return [points[index] for index in sorted(indexes)]


def _summary(values):
    if not values or not any(values):
        return {
            "status": "NO_METRIC",
            "sample_count": len(values),
            "average": None,
            "median": None,
            "high": None,
            "maximum": None,
        }
    ordered = sorted(values)
    high_index = round((len(ordered) - 1) * 0.75)
    return {
        "status": "READY" if len(values) >= 2 else "INSUFFICIENT",
        "sample_count": len(values),
        "average": fmean(values),
        "median": median(values),
        "high": ordered[high_index],
        "maximum": ordered[-1],
    }


def _group_summary(videos, metric):
    values = [_number(video.get(metric)) for video in videos]
    return {
        "sample_count": len(values),
        "average": fmean(values) if values else None,
        "median": median(values) if values else None,
    }


def build_uploader_visualization(
    videos,
    ranked_bvids,
    analysis,
    metric="views",
    max_points=MAX_SERIES_POINTS,
):
    """Build bounded chart data without network access or storage writes."""
    if metric not in METRIC_LABELS:
        raise ValueError(f"unsupported uploader metric: {metric}")
    if not 1 <= int(max_points) <= MAX_SERIES_POINTS:
        raise ValueError("max_points must be between 1 and 240")

    ordered = _ordered_videos(videos)
    ranked = set(ranked_bvids or ())
    monthly = Counter(
        video["published_at"][:7]
        for video in ordered
        if video.get("published_at")
    )
    monthly_frequency = [
        {
            "at": datetime.fromisoformat(f"{month}-01T00:00:00+00:00"),
            "label": month,
            "value": count,
        }
        for month, count in sorted(monthly.items())
    ]
    points = [
        {
            "at": datetime.fromisoformat(video["published_at"]),
            "value": _number(video.get(metric)),
            "bvid": video.get("bvid"),
            "title": video.get("title") or video.get("bvid") or "未命名视频",
            "ranked": video.get("bvid") in ranked,
        }
        for video in ordered
        if video.get("published_at")
    ]
    performance = _sample_points(points, int(max_points))
    metric_values = [point["value"] for point in points]
    ranked_videos = [video for video in ordered if video.get("bvid") in ranked]
    normal_videos = [video for video in ordered if video.get("bvid") not in ranked]
    comparison = {
        "status": "READY" if ranked_videos and normal_videos else "INSUFFICIENT",
        "ranked": _group_summary(ranked_videos, metric),
        "normal": _group_summary(normal_videos, metric),
    }
    viral_bvids = set((analysis or {}).get("viral_bvids") or ())
    viral_count = sum(video.get("bvid") in viral_bvids for video in ordered)
    return {
        "status": "READY" if ordered else "NO_DATA",
        "metric": metric,
        "metric_label": METRIC_LABELS[metric],
        "sample_count": len(ordered),
        "range_start": ordered[0].get("published_at") if ordered else None,
        "range_end": ordered[-1].get("published_at") if ordered else None,
        "updated_at": max(
            (video.get("updated_at") or "" for video in ordered), default=""
        )
        or None,
        "monthly_frequency": monthly_frequency,
        "performance_status": (
            "READY" if metric_values and any(metric_values) else "NO_METRIC"
        ),
        "performance_series": performance,
        "distribution": _summary(metric_values),
        "comparison": comparison,
        "viral": {
            "status": "READY" if len(ordered) >= 4 else "INSUFFICIENT",
            "sample_count": len(ordered),
            "count": viral_count,
            "ratio": float((analysis or {}).get("viral_ratio") or 0),
            "threshold": (analysis or {}).get("viral_threshold"),
        },
    }


__all__ = ["MAX_SERIES_POINTS", "METRIC_LABELS", "build_uploader_visualization"]
