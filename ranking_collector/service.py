#!/usr/bin/env python 3.12
# -*- coding: utf-8 -*-

"""Ranking Collector 的采集、保存和趋势计算业务逻辑。"""

import random
import time
from datetime import datetime

from ranking_collector.config import (
    DATABASE_PATH,
    PARTITIONS,
    TIMEZONE,
    TOP_N,
)
from ranking_collector.models import (
    MetricChange,
    RankingItem,
    RankingSnapshot,
    collection_failure,
    collection_success,
    ranking_item_from_bilibili,
    validate_datetime,
)
from ranking_collector.repository import (
    create_collection_run,
    finish_collection_run,
    get_latest_successful_snapshot,
    initialize_database,
    save_snapshot,
)


class CollectionServiceError(RuntimeError):
    """整轮采集没有全部成功。"""


PARTITION_DELAY_MIN_SECONDS = 1.5
PARTITION_DELAY_MAX_SECONDS = 3.0


def wait_between_partitions():
    """在分区请求之间随机等待，降低连续请求触发风控的概率。"""
    wait_seconds = random.uniform(
        PARTITION_DELAY_MIN_SECONDS,
        PARTITION_DELAY_MAX_SECONDS,
    )
    time.sleep(wait_seconds)


def get_enabled_partitions(partitions=PARTITIONS):
    """返回启用的分区配置。"""
    enabled_partitions = []

    for partition_key, partition_config in partitions.items():
        if not partition_config.get("enabled", False):
            continue

        name = partition_config.get("name")
        rid = partition_config.get("rid")

        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"分区 {partition_key} 缺少有效 name")
        if not isinstance(rid, int) or isinstance(rid, bool) or rid < 0:
            raise ValueError(f"分区 {partition_key} 缺少有效 rid")

        enabled_partitions.append(
            {
                "key": partition_key,
                "name": name,
                "rid": rid,
            }
        )

    if not enabled_partitions:
        raise ValueError("没有启用的采集分区")

    return enabled_partitions


def calculate_metric_change(previous_item, current_item):
    """计算同一视频在两个时间点之间的指标变化。"""
    if not isinstance(previous_item, RankingItem):
        raise TypeError("previous_item 必须是 RankingItem")
    if not isinstance(current_item, RankingItem):
        raise TypeError("current_item 必须是 RankingItem")
    if previous_item.video.bvid != current_item.video.bvid:
        raise ValueError("只能比较同一个 BV 号的视频")
    if previous_item.video.partition != current_item.video.partition:
        raise ValueError("只能比较同一分区中的视频")
    if current_item.collected_at <= previous_item.collected_at:
        raise ValueError("当前采集时间必须晚于上一次采集时间")

    elapsed_hours = (
        current_item.collected_at - previous_item.collected_at
    ).total_seconds() / 3600
    views_delta = (
        current_item.metrics.views - previous_item.metrics.views
    )

    return MetricChange(
        bvid=current_item.video.bvid,
        views_delta=views_delta,
        likes_delta=(
            current_item.metrics.likes - previous_item.metrics.likes
        ),
        coins_delta=(
            current_item.metrics.coins - previous_item.metrics.coins
        ),
        favorites_delta=(
            current_item.metrics.favorites
            - previous_item.metrics.favorites
        ),
        comments_delta=(
            current_item.metrics.comments
            - previous_item.metrics.comments
        ),
        danmaku_delta=(
            current_item.metrics.danmaku
            - previous_item.metrics.danmaku
        ),
        shares_delta=(
            current_item.metrics.shares - previous_item.metrics.shares
        ),
        rank_change=previous_item.rank - current_item.rank,
        elapsed_hours=elapsed_hours,
        views_per_hour=views_delta / elapsed_hours,
    )


def calculate_snapshot_changes(previous_snapshot, current_snapshot):
    """比较同一分区的两份快照，只返回两次都出现的视频。"""
    if previous_snapshot is None:
        return []
    if not isinstance(previous_snapshot, RankingSnapshot):
        raise TypeError("previous_snapshot 必须是 RankingSnapshot")
    if not isinstance(current_snapshot, RankingSnapshot):
        raise TypeError("current_snapshot 必须是 RankingSnapshot")
    if previous_snapshot.partition != current_snapshot.partition:
        raise ValueError("只能比较同一分区的快照")

    previous_items = {
        item.video.bvid: item
        for item in previous_snapshot.items
    }
    changes = []

    for current_item in current_snapshot.items:
        previous_item = previous_items.get(current_item.video.bvid)
        if previous_item is None:
            continue
        changes.append(
            calculate_metric_change(previous_item, current_item)
        )

    return changes


def build_snapshot(raw_items, partition_name, collected_at):
    """把一个分区的 Bilibili 原始列表转换为完整快照。"""
    if not isinstance(raw_items, list):
        raise TypeError("raw_items 必须是列表")
    validate_datetime(collected_at, "collected_at")

    ranking_items = []
    for rank, raw_item in enumerate(raw_items, start=1):
        ranking_items.append(
            ranking_item_from_bilibili(
                raw=raw_item,
                partition=partition_name,
                rank=rank,
                collected_at=collected_at,
            )
        )

    return RankingSnapshot(
        partition=partition_name,
        collected_at=collected_at,
        items=ranking_items,
    )


def get_fetch_function(fetch_function=None):
    """获取 client 采集函数；延迟导入便于独立使用 service。"""
    if fetch_function is None:
        from ranking_collector.client import fetch_ranking

        return fetch_ranking
    if not callable(fetch_function):
        raise TypeError("fetch_function 必须是可调用函数")
    return fetch_function


def collect_once(
    collected_at=None,
    fetch_function=None,
    database_path=DATABASE_PATH,
    partitions=PARTITIONS,
    limit=TOP_N,
):
    """执行一整轮采集，并返回任务与各分区的结果。"""
    if collected_at is None:
        collected_at = datetime.now(TIMEZONE)
    else:
        validate_datetime(collected_at, "collected_at")
        collected_at = collected_at.astimezone(TIMEZONE)

    fetch_function = get_fetch_function(fetch_function)
    enabled_partitions = get_enabled_partitions(partitions)

    initialize_database(database_path)
    run_id = create_collection_run(collected_at, database_path)
    partition_results = []
    failure_messages = []

    partition_count = len(enabled_partitions)

    for partition_index, partition in enumerate(enabled_partitions):
        partition_name = partition["name"]

        try:
            previous_snapshot = get_latest_successful_snapshot(
                partition=partition_name,
                before=collected_at,
                database_path=database_path,
            )
            raw_items = fetch_function(
                rid=partition["rid"],
                limit=limit,
            )
            snapshot = build_snapshot(
                raw_items=raw_items,
                partition_name=partition_name,
                collected_at=collected_at,
            )
            save_snapshot(
                run_id=run_id,
                snapshot=snapshot,
                database_path=database_path,
            )
            changes = calculate_snapshot_changes(
                previous_snapshot,
                snapshot,
            )
            result = collection_success(snapshot)
        except Exception as error:
            error_message = str(error) or error.__class__.__name__
            result = collection_failure(
                partition=partition_name,
                collected_at=collected_at,
                error_message=error_message,
            )
            previous_snapshot = None
            changes = []
            failure_messages.append(
                f"{partition_name}: {error_message}"
            )

        partition_results.append(
            {
                "key": partition["key"],
                "name": partition_name,
                "rid": partition["rid"],
                "result": result,
                "previous_snapshot": previous_snapshot,
                "changes": changes,
            }
        )

        if partition_index + 1 < partition_count:
            wait_between_partitions()

    succeeded = not failure_messages
    error_message = None
    if failure_messages:
        error_message = "; ".join(failure_messages)

    finish_collection_run(
        run_id=run_id,
        succeeded=succeeded,
        error_message=error_message,
        database_path=database_path,
    )

    return {
        "run_id": run_id,
        "collected_at": collected_at,
        "succeeded": succeeded,
        "error_message": error_message,
        "partitions": partition_results,
    }


def get_last_success_at(
    database_path=DATABASE_PATH,
    partitions=PARTITIONS,
):
    """返回所有启用分区都已覆盖的最近采集时间。"""
    initialize_database(database_path)
    snapshot_times = []

    for partition in get_enabled_partitions(partitions):
        snapshot = get_latest_successful_snapshot(
            partition=partition["name"],
            database_path=database_path,
        )
        if snapshot is None:
            return None
        snapshot_times.append(snapshot.collected_at)

    return min(snapshot_times)


def run_scheduled_collection(
    scheduled_at,
    fetch_function=None,
    database_path=DATABASE_PATH,
    partitions=PARTITIONS,
    limit=TOP_N,
):
    """供 scheduler 调用；整轮未全部成功时抛出明确异常。"""
    validate_datetime(scheduled_at, "scheduled_at")
    result = collect_once(
        fetch_function=fetch_function,
        database_path=database_path,
        partitions=partitions,
        limit=limit,
    )
    result["scheduled_at"] = scheduled_at

    if not result["succeeded"]:
        raise CollectionServiceError(result["error_message"])

    return result


__all__ = [
    "CollectionServiceError",
    "build_snapshot",
    "calculate_metric_change",
    "calculate_snapshot_changes",
    "collect_once",
    "get_enabled_partitions",
    "get_last_success_at",
    "run_scheduled_collection",
    "wait_between_partitions",
]
