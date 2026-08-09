#!/usr/bin/env python 3.12
# -*- coding: utf-8 -*-

"""Ranking Collector 内部使用的统一数据模型。"""

from collections.abc import Mapping
from datetime import UTC, datetime
from math import isfinite


def validate_text(value, field_name):
    """校验必填字符串。"""
    if not isinstance(value, str):
        raise TypeError(f"{field_name} 必须是字符串")
    if not value.strip():
        raise ValueError(f"{field_name} 不能为空")
    return value


def validate_datetime(value, field_name):
    """校验带时区的 datetime。"""
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} 必须是 datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} 必须包含时区")
    return value


def validate_non_negative_integer(value, field_name):
    """校验非负整数，明确拒绝 bool。"""
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{field_name} 必须是整数")
    if value < 0:
        raise ValueError(f"{field_name} 不能小于 0")
    return value


def get_required_value(data, key):
    """从映射中读取必填字段。"""
    if key not in data or data[key] is None:
        raise ValueError(f"缺少关键字段 {key}")
    return data[key]


def get_metric_value(stat, key):
    """读取指标；字段缺失或为 None 时使用 0。"""
    value = stat.get(key)
    if value is None:
        return 0
    return validate_non_negative_integer(value, f"stat.{key}")


class VideoInfo:
    """视频基础信息。"""

    def __init__(self, bvid, title, uploader, partition, published_at):
        self.bvid = validate_text(bvid, "bvid")
        self.title = validate_text(title, "title")
        self.uploader = validate_text(uploader, "uploader")
        self.partition = validate_text(partition, "partition")
        self.published_at = validate_datetime(
            published_at,
            "published_at",
        )


class VideoMetrics:
    """某个时间点的视频累计指标。"""

    def __init__(
        self,
        views=0,
        likes=0,
        coins=0,
        favorites=0,
        comments=0,
        danmaku=0,
        shares=0,
    ):
        self.views = validate_non_negative_integer(views, "views")
        self.likes = validate_non_negative_integer(likes, "likes")
        self.coins = validate_non_negative_integer(coins, "coins")
        self.favorites = validate_non_negative_integer(
            favorites,
            "favorites",
        )
        self.comments = validate_non_negative_integer(
            comments,
            "comments",
        )
        self.danmaku = validate_non_negative_integer(danmaku, "danmaku")
        self.shares = validate_non_negative_integer(shares, "shares")


class RankingItem:
    """榜单中的一条视频记录。"""

    def __init__(self, video, metrics, rank, collected_at):
        if not isinstance(video, VideoInfo):
            raise TypeError("video 必须是 VideoInfo")
        if not isinstance(metrics, VideoMetrics):
            raise TypeError("metrics 必须是 VideoMetrics")

        rank = validate_non_negative_integer(rank, "rank")
        if rank == 0:
            raise ValueError("rank 必须大于 0")

        self.video = video
        self.metrics = metrics
        self.rank = rank
        self.collected_at = validate_datetime(
            collected_at,
            "collected_at",
        )

class RankingSnapshot:
    """某个分区在一个采集时间点的完整榜单快照。"""

    def __init__(self, partition, collected_at, items):
        self.partition = validate_text(partition, "partition")
        self.collected_at = validate_datetime(
            collected_at,
            "collected_at",
        )

        try:
            self.items = list(items)
        except TypeError as error:
            raise TypeError("items 必须是 RankingItem 集合") from error

        used_ranks = set()
        used_bvids = set()

        for item in self.items:
            if not isinstance(item, RankingItem):
                raise TypeError("items 只能包含 RankingItem")
            if item.video.partition != self.partition:
                raise ValueError("榜单条目的分区必须与快照一致")
            if item.collected_at != self.collected_at:
                raise ValueError("榜单条目的采集时间必须与快照一致")
            if item.rank in used_ranks:
                raise ValueError("同一快照内的排名不能重复")
            if item.video.bvid in used_bvids:
                raise ValueError("同一快照内的 BV 号不能重复")

            used_ranks.add(item.rank)
            used_bvids.add(item.video.bvid)


class MetricChange:
    """同一视频在两个采集时间点之间的指标变化。"""

    def __init__(
        self,
        bvid,
        views_delta,
        likes_delta,
        coins_delta,
        favorites_delta,
        comments_delta,
        danmaku_delta,
        shares_delta,
        rank_change,
        elapsed_hours,
        views_per_hour,
    ):
        self.bvid = validate_text(bvid, "bvid")
        self.views_delta = self.validate_delta(
            views_delta,
            "views_delta",
        )
        self.likes_delta = self.validate_delta(likes_delta, "likes_delta")
        self.coins_delta = self.validate_delta(coins_delta, "coins_delta")
        self.favorites_delta = self.validate_delta(
            favorites_delta,
            "favorites_delta",
        )
        self.comments_delta = self.validate_delta(
            comments_delta,
            "comments_delta",
        )
        self.danmaku_delta = self.validate_delta(
            danmaku_delta,
            "danmaku_delta",
        )
        self.shares_delta = self.validate_delta(
            shares_delta,
            "shares_delta",
        )
        self.rank_change = self.validate_delta(rank_change, "rank_change")

        if (
            not isinstance(elapsed_hours, (int, float))
            or isinstance(elapsed_hours, bool)
        ):
            raise TypeError("elapsed_hours 必须是数字")
        if not isfinite(elapsed_hours) or elapsed_hours <= 0:
            raise ValueError("elapsed_hours 必须是大于 0 的有限数值")

        if (
            not isinstance(views_per_hour, (int, float))
            or isinstance(views_per_hour, bool)
        ):
            raise TypeError("views_per_hour 必须是数字")
        if not isfinite(views_per_hour):
            raise ValueError("views_per_hour 必须是有限数值")

        self.elapsed_hours = elapsed_hours
        self.views_per_hour = views_per_hour

    def validate_delta(self, value, field_name):
        """变化量允许为正数、零或负数。"""
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError(f"{field_name} 必须是整数")
        return value

    def get_interaction_delta(self):
        """计算各项互动指标的总增量。"""
        return (
            self.likes_delta
            + self.coins_delta
            + self.favorites_delta
            + self.comments_delta
            + self.danmaku_delta
            + self.shares_delta
        )


class CollectionResult:
    """单个分区的一次采集结果。"""

    def __init__(
        self,
        partition,
        collected_at,
        succeeded,
        snapshot=None,
        error_message=None,
    ):
        self.partition = validate_text(partition, "partition")
        self.collected_at = validate_datetime(
            collected_at,
            "collected_at",
        )

        if not isinstance(succeeded, bool):
            raise TypeError("succeeded 必须是布尔值")
        self.succeeded = succeeded

        if succeeded:
            if not isinstance(snapshot, RankingSnapshot):
                raise ValueError("成功的采集结果必须包含 snapshot")
            if error_message is not None:
                raise ValueError("成功的采集结果不能包含 error_message")
            if snapshot.partition != self.partition:
                raise ValueError("结果分区必须与 snapshot 一致")
            if snapshot.collected_at != self.collected_at:
                raise ValueError("结果采集时间必须与 snapshot 一致")
        else:
            if snapshot is not None:
                raise ValueError("失败的采集结果不能包含 snapshot")
            validate_text(error_message, "error_message")

        self.snapshot = snapshot
        self.error_message = error_message

def ranking_item_from_bilibili(
    raw,
    partition,
    rank,
    collected_at,
):
    """把一条 Bilibili 原始数据转换为榜单条目。"""
    if not isinstance(raw, Mapping):
        raise TypeError("raw 必须是字典或其他映射")

    bvid = validate_text(
        get_required_value(raw, "bvid"),
        "bvid",
    )
    title = validate_text(
        get_required_value(raw, "title"),
        "title",
    )

    owner = raw.get("owner")
    if not isinstance(owner, Mapping):
        raise ValueError("owner 必须是字典或其他映射")
    uploader = validate_text(
        get_required_value(owner, "name"),
        "owner.name",
    )

    pubdate = get_required_value(raw, "pubdate")
    if not isinstance(pubdate, (int, float)) or isinstance(pubdate, bool):
        raise TypeError("pubdate 必须是 Unix 时间戳")
    if not isfinite(pubdate):
        raise ValueError("pubdate 必须是有限数值")
    try:
        published_at = datetime.fromtimestamp(pubdate, tz=UTC)
    except (OverflowError, OSError, ValueError) as error:
        raise ValueError("pubdate 超出有效范围") from error

    stat = raw.get("stat")
    if stat is None:
        stat = {}
    elif not isinstance(stat, Mapping):
        raise TypeError("stat 必须是字典或其他映射")

    video = VideoInfo(
        bvid=bvid,
        title=title,
        uploader=uploader,
        partition=partition,
        published_at=published_at,
    )
    metrics = VideoMetrics(
        views=get_metric_value(stat, "view"),
        likes=get_metric_value(stat, "like"),
        coins=get_metric_value(stat, "coin"),
        favorites=get_metric_value(stat, "favorite"),
        comments=get_metric_value(stat, "reply"),
        danmaku=get_metric_value(stat, "danmaku"),
        shares=get_metric_value(stat, "share"),
    )

    return RankingItem(
        video=video,
        metrics=metrics,
        rank=rank,
        collected_at=collected_at,
    )


def collection_success(snapshot):
    """创建成功的采集结果。"""
    if not isinstance(snapshot, RankingSnapshot):
        raise TypeError("snapshot 必须是 RankingSnapshot")
    return CollectionResult(
        partition=snapshot.partition,
        collected_at=snapshot.collected_at,
        succeeded=True,
        snapshot=snapshot,
    )


def collection_failure(partition, collected_at, error_message):
    """创建失败的采集结果。"""
    return CollectionResult(
        partition=partition,
        collected_at=collected_at,
        succeeded=False,
        error_message=error_message,
    )


__all__ = [
    "CollectionResult",
    "MetricChange",
    "RankingItem",
    "RankingSnapshot",
    "VideoInfo",
    "VideoMetrics",
    "collection_failure",
    "collection_success",
    "ranking_item_from_bilibili",
]
