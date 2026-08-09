#!/usr/bin/env python 3.12
# -*- coding: utf-8 -*-

"""Ranking Collector 命令行启动入口。"""

import argparse
import logging

from ranking_collector.config import DATABASE_PATH
from ranking_collector.scheduler import RankingScheduler
from ranking_collector.service import (
    collect_once,
    get_last_success_at,
    run_scheduled_collection,
)


logger = logging.getLogger(__name__)


def configure_logging():
    """配置命令行运行时的基础日志格式。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


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
            change_count = len(partition["changes"])
            output_function(
                f"  [成功] {partition['name']}："
                f"保存 {item_count} 条，计算 {change_count} 条变化"
            )
        else:
            output_function(
                f"  [失败] {partition['name']}："
                f"{partition_result.error_message}"
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
        logger.info("收到停止信号，Ranking Collector 已退出")
        return 0
    except Exception:
        logger.exception("Ranking Collector 运行失败")
        return 1


if __name__ == "__main__":
    # 开发阶段直接运行本文件时，立即采集一次真实数据。
    # 采集结果会保存到 config.py 配置的 data/ranking.db。
    configure_logging()
    test_result = run_once()

    if test_result["succeeded"]:
        print("Ranking Collector 测试跑通")
    else:
        print("Ranking Collector 测试未完全成功")
