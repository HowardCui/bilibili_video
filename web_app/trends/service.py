"""Pure calculations for long-term ranking trends."""

import math
from datetime import timedelta

METRICS = {
    "views",
    "likes",
    "coins",
    "favorites",
    "comments",
    "danmaku",
    "shares",
}
STALE_AFTER = timedelta(hours=24)


def aggregate_series(points, max_points, mode):
    """Reduce an ordered series without exceeding the chart point limit."""
    if mode not in {"last", "mean"}:
        raise ValueError("mode is not supported")
    if not isinstance(max_points, int) or isinstance(max_points, bool):
        raise TypeError("max_points must be an integer")
    if max_points < 1:
        raise ValueError("max_points must be positive")
    if len(points) <= max_points:
        return list(points)
    bucket_size = math.ceil(len(points) / max_points)
    result = []
    for start in range(0, len(points), bucket_size):
        bucket = points[start : start + bucket_size]
        if mode == "last":
            result.append(dict(bucket[-1]))
            continue
        values = [point["value"] for point in bucket if point["value"] is not None]
        merged = dict(bucket[-1])
        merged["value"] = sum(values) / len(values) if values else None
        result.append(merged)
    if result and result[-1]["at"] != points[-1]["at"]:
        result[-1] = dict(points[-1])
    return result


def _items_by_bvid(snapshot):
    return {item["bvid"]: item for item in snapshot["items"]}


def _video_occurrences(snapshots):
    occurrences = {}
    for index, snapshot in enumerate(snapshots):
        for item in snapshot["items"]:
            occurrences.setdefault(item["bvid"], []).append(
                (index, snapshot["collected_at"], item)
            )
    return occurrences


def _video_summary(bvid, snapshots, occurrences):
    records = occurrences[bvid]
    indices = [record[0] for record in records]
    trailing = 1
    for left, right in zip(reversed(indices[:-1]), reversed(indices[1:]), strict=False):
        if right - left != 1:
            break
        trailing += 1
    reentry_count = sum(
        right - left > 1 for left, right in zip(indices, indices[1:], strict=False)
    )
    latest_item = records[-1][2]
    newest_items = _items_by_bvid(snapshots[-1]) if snapshots else {}
    ranks = [record[2]["rank"] for record in records]
    return {
        "bvid": bvid,
        "title": latest_item["title"],
        "uploader": latest_item["uploader"],
        "first_ranked_at": records[0][1],
        "last_ranked_at": records[-1][1],
        "consecutive_count": trailing,
        "cumulative_count": len(records),
        "best_rank": min(ranks),
        "worst_rank": max(ranks),
        "current_rank": (newest_items[bvid]["rank"] if bvid in newest_items else None),
        "reentry_count": reentry_count,
    }


def _video_choices(snapshots, occurrences):
    if not snapshots:
        return []
    latest = sorted(snapshots[-1]["items"], key=lambda item: item["rank"])
    ordered = [item["bvid"] for item in latest]
    remaining = sorted(
        (bvid for bvid in occurrences if bvid not in ordered),
        key=lambda bvid: (-len(occurrences[bvid]), bvid),
    )
    ordered.extend(remaining)
    return [
        {
            "bvid": bvid,
            "title": occurrences[bvid][-1][2]["title"],
        }
        for bvid in ordered
    ]


def _turnover_series(snapshots):
    result = []
    previous = None
    for snapshot in snapshots:
        point = {
            "at": snapshot["collected_at"],
            "value": None,
            "status": "NO_BASELINE",
        }
        if previous is not None:
            interval = snapshot["collected_at"] - previous["collected_at"]
            if interval > STALE_AFTER:
                point["status"] = "STALE"
            else:
                previous_bvids = set(_items_by_bvid(previous))
                current_bvids = set(_items_by_bvid(snapshot))
                point["value"] = (
                    len(previous_bvids - current_bvids) / len(previous_bvids)
                    if previous_bvids
                    else 0.0
                )
                point["status"] = "VALID"
        result.append(point)
        previous = snapshot
    return result


def _missing_intervals(history):
    intervals = [
        {
            "start": result["collected_at"],
            "end": result["collected_at"],
            "reason": "COLLECTION_FAILED",
        }
        for result in history.get("partition_results", [])
        if not result.get("succeeded")
    ]
    snapshots = history["snapshots"]
    for previous, current in zip(snapshots, snapshots[1:], strict=False):
        if current["collected_at"] - previous["collected_at"] > STALE_AFTER:
            intervals.append(
                {
                    "start": previous["collected_at"],
                    "end": current["collected_at"],
                    "reason": "STALE_GAP",
                }
            )
    return sorted(intervals, key=lambda item: item["start"])


def _trend_lists(occurrences):
    summaries = []
    first_entries = []
    reentries = []
    for bvid, records in occurrences.items():
        latest = records[-1][2]
        summaries.append(
            {
                "bvid": bvid,
                "title": latest["title"],
                "count": len(records),
            }
        )
        first_entries.append(
            {"bvid": bvid, "title": records[0][2]["title"], "at": records[0][1]}
        )
        for previous, current in zip(records, records[1:], strict=False):
            if current[0] - previous[0] > 1:
                reentries.append(
                    {"bvid": bvid, "title": current[2]["title"], "at": current[1]}
                )
    return {
        "long_running": sorted(
            summaries, key=lambda item: (-item["count"], item["bvid"])
        ),
        "first_entries": sorted(
            first_entries, key=lambda item: item["at"], reverse=True
        ),
        "reentries": sorted(reentries, key=lambda item: item["at"], reverse=True),
    }


def build_trend_page_data(
    history,
    selected_bvid=None,
    metric="views",
    max_points=240,
):
    """Build one complete trend page model from ordered history facts."""
    if metric not in METRICS:
        raise ValueError("metric is not supported")
    snapshots = list(history.get("snapshots") or [])
    occurrences = _video_occurrences(snapshots)
    choices = _video_choices(snapshots, occurrences)
    valid_bvids = {choice["bvid"] for choice in choices}
    if selected_bvid not in valid_bvids:
        selected_bvid = choices[0]["bvid"] if choices else None

    rank_series = []
    metric_series = []
    summary = None
    if selected_bvid is not None:
        summary = _video_summary(selected_bvid, snapshots, occurrences)
        for snapshot in snapshots:
            current = _items_by_bvid(snapshot).get(selected_bvid)
            rank_series.append(
                {
                    "at": snapshot["collected_at"],
                    "value": current["rank"] if current else None,
                }
            )
            metric_series.append(
                {
                    "at": snapshot["collected_at"],
                    "value": current[metric] if current else None,
                }
            )

    turnover = _turnover_series(snapshots)
    heat = [
        {
            "at": snapshot["collected_at"],
            "value": sum(item["views"] for item in snapshot["items"]),
            "item_count": len(snapshot["items"]),
        }
        for snapshot in snapshots
    ]
    status = "AVAILABLE"
    if not snapshots:
        status = "NO_DATA"
    elif len(snapshots) < 2:
        status = "INSUFFICIENT_DATA"
    return {
        "status": status,
        "partition": history.get("partition"),
        "range_key": history.get("range_key"),
        "metric": metric,
        "metadata": {
            "started_at": history.get("started_at"),
            "ended_at": history.get("ended_at"),
            "snapshot_count": len(snapshots),
            "latest_snapshot_at": (
                snapshots[-1]["collected_at"] if snapshots else None
            ),
            "truncated": bool(history.get("truncated")),
        },
        "video_choices": choices,
        "selected_bvid": selected_bvid,
        "video_summary": summary,
        "rank_series": aggregate_series(rank_series, max_points, "last"),
        "metric_series": aggregate_series(metric_series, max_points, "last"),
        "turnover_series": aggregate_series(turnover, max_points, "mean"),
        "heat_series": aggregate_series(heat, max_points, "last"),
        "lists": _trend_lists(occurrences),
        "missing_intervals": _missing_intervals(history),
    }


__all__ = ["aggregate_series", "build_trend_page_data"]
