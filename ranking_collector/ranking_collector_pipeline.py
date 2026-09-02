#!/usr/bin/env python 3.12
# -*- coding: utf-8 -*-

"""Ranking Collector 命令行启动入口。"""

import argparse

from app_logging import (
    configure_logging as configure_project_logging,
)
from app_logging import (
    get_logger,
    log_event,
)
from ranking_collector.config import DATABASE_PATH
from ranking_collector.models import ComparisonSource, ComparisonStatus
from ranking_collector.scheduler import RankingScheduler
from ranking_collector.service import (
    collect_once,
    get_last_success_at,
    run_scheduled_collection,
)

logger = get_logger("ranking.pipeline")


def configure_logging():
    """配置排行榜命令行的统一结构化日志。"""
    return configure_project_logging("ranking")


def parse_arguments(arguments=None):
    """解析 --once 和 --schedule 两种运行方式。"""
    parser = argparse.ArgumentParser(
        prog="python -m ranking_collector.ranking_collector_pipeline",
        description="采集并保存 Bilibili 热门排行榜快照",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--once",
        action="store_true",
        help="立即执行一轮采集后退出",
    )
    mode.add_argument(
        "--schedule",
        action="store_true",
        help="启动每天 00、06、12、18 点执行的长期调度器",
    )
    return parser.parse_args(arguments)


def format_video(item):
    """格式化榜单视频标题和 BV 号。"""
    return f"{item.video.title}（{item.video.bvid}）"


def print_video_group(label, items, output_function):
    """输出一组榜单视频及数量。"""
    output_function(f"    {label}：{len(items)}")
    for index, item in enumerate(items, start=1):
        output_function(f"      {index}. {format_video(item)}")


def print_comparison(comparison, output_function=print):
    """输出一份快照比较的状态、成员变化和增长排行。"""
    if comparison is None:
        output_function("    没有可用的历史比较")
        return

    if comparison.source == ComparisonSource.LAST_VALID:
        output_function("    本轮采集失败，以下为上一份有效比较")

    output_function(f"    比较来源：{comparison.source}")
    output_function(f"    比较状态：{comparison.status}")
    if comparison.previous_collected_at is not None:
        output_function(
            "    快照区间："
            f"{comparison.previous_collected_at.isoformat()} → "
            f"{comparison.current_collected_at.isoformat()}"
        )

    if comparison.status == ComparisonStatus.NO_BASELINE:
        output_function("    第一次采集，没有历史比较基准")
        return
    if comparison.status == ComparisonStatus.EMPTY_CURRENT:
        output_function("    当前榜单为空，不计算成员进出和换血率")
        return

    print_video_group("持续在榜", comparison.retained, output_function)
    print_video_group("新进榜", comparison.entered, output_function)
    print_video_group("跌出榜", comparison.exited, output_function)

    current_items = {
        item.video.bvid: item for item in comparison.retained
    }
    ranking_fallers = sorted(
        (
            change
            for change in comparison.metric_changes
            if change.rank_change < 0
        ),
        key=lambda change: (
            change.rank_change,
            current_items[change.bvid].rank,
        ),
    )

    output_function(f"    排名上升：{len(comparison.ranking_risers)}")
    for index, change in enumerate(comparison.ranking_risers, start=1):
        item = current_items[change.bvid]
        output_function(
            f"      {index}. {format_video(item)} "
            f"上升 {change.rank_change} 名"
        )

    output_function(f"    排名下降：{len(ranking_fallers)}")
    for index, change in enumerate(ranking_fallers, start=1):
        item = current_items[change.bvid]
        output_function(
            f"      {index}. {format_video(item)} "
            f"下降 {abs(change.rank_change)} 名"
        )

    output_function("    每小时播放增长：")
    for index, change in enumerate(
        comparison.views_growth_ranking, start=1
    ):
        item = current_items[change.bvid]
        output_function(
            f"      {index}. {format_video(item)} "
            f"{change.views_per_hour:+,.0f} 播放/小时"
        )

    if comparison.turnover_rate is None:
        if comparison.status == ComparisonStatus.STALE:
            output_function(
                "    换血率：不可用"
                f"（快照间隔 {comparison.elapsed_hours:.1f} 小时）"
            )
        else:
            output_function("    换血率：不可用")
    else:
        output_function(f"    换血率：{comparison.turnover_rate:.2%}")


def print_collection_summary(result, output_function=print):
    """输出一轮采集的简洁结果。"""
    status = "成功" if result["succeeded"] else "失败"
    output_function(
        f"采集任务 #{result['run_id']} {status}，"
        f"采集时间：{result['collected_at'].isoformat()}"
    )

    for partition in result["partitions"]:
        partition_result = partition["result"]

        if partition_result.succeeded:
            item_count = len(partition_result.snapshot.items)
            output_function(
                f"  [成功] {partition['name']}："
                f"保存 {item_count} 条"
            )
        else:
            output_function(
                f"  [失败] {partition['name']}："
                f"{partition_result.error_message}"
            )

        print_comparison(
            partition.get("comparison"),
            output_function=output_function,
        )

    if result["error_message"]:
        output_function(f"错误汇总：{result['error_message']}")


def run_once(fetch_function=None, database_path=DATABASE_PATH):
    """立即执行一轮采集，输出并返回结果。"""
    result = collect_once(
        fetch_function=fetch_function,
        database_path=database_path,
    )
    print_collection_summary(result)
    return result


def run_schedule(fetch_function=None, database_path=DATABASE_PATH):
    """组装 service 和 scheduler，并持续运行。"""
    def collect_for_schedule(scheduled_at):
        result = run_scheduled_collection(
            scheduled_at=scheduled_at,
            fetch_function=fetch_function,
            database_path=database_path,
        )
        print_collection_summary(result)
        return result

    def read_last_success_at():
        return get_last_success_at(database_path=database_path)

    scheduler = RankingScheduler(
        collect_function=collect_for_schedule,
        get_last_success_at=read_last_success_at,
    )
    scheduler.run_forever()


def main(arguments=None):
    """命令行主函数，返回进程退出码。"""
    configure_logging()
    options = parse_arguments(arguments)

    try:
        if options.once:
            result = run_once()
            return 0 if result["succeeded"] else 1

        run_schedule()
        return 0
    except KeyboardInterrupt:
        log_event(
            logger,
            "INFO",
            "ranking_process_stopped",
            "收到停止信号，Ranking Collector 已退出",
            task_type="ranking",
        )
        return 0
    except Exception:
        log_event(
            logger,
            "ERROR",
            "ranking_process_failed",
            "Ranking Collector 运行失败",
            task_type="ranking",
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
