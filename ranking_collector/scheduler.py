#!/usr/bin/env python 3.12
# -*- coding: utf-8 -*-

"""Ranking Collector 的定时调度逻辑。"""

import logging
import threading
from datetime import datetime, timedelta, tzinfo

from ranking_collector.config import SCHEDULE_HOURS, TIMEZONE
from ranking_collector.models import validate_datetime


logger = logging.getLogger(__name__)


def validate_schedule_hours(schedule_hours):
    """校验并整理每天执行的小时列表。"""
    try:
        hours = tuple(sorted(set(schedule_hours)))
    except TypeError as error:
        raise TypeError("schedule_hours 必须是整数集合") from error

    if not hours:
        raise ValueError("schedule_hours 不能为空")

    for hour in hours:
        if not isinstance(hour, int) or isinstance(hour, bool):
            raise TypeError("schedule_hours 只能包含整数")
        if hour < 0 or hour > 23:
            raise ValueError("采集小时必须在 0 到 23 之间")

    return hours


def get_latest_schedule_time(now, schedule_hours=SCHEDULE_HOURS):
    """返回不晚于 now 的最近一个计划执行时间。"""
    validate_datetime(now, "now")
    hours = validate_schedule_hours(schedule_hours)

    for hour in reversed(hours):
        candidate = now.replace(
            hour=hour,
            minute=0,
            second=0,
            microsecond=0,
        )
        if candidate <= now:
            return candidate

    previous_day = now - timedelta(days=1)
    return previous_day.replace(
        hour=hours[-1],
        minute=0,
        second=0,
        microsecond=0,
    )


def get_next_schedule_time(now, schedule_hours=SCHEDULE_HOURS):
    """返回严格晚于 now 的下一个计划执行时间。"""
    validate_datetime(now, "now")
    hours = validate_schedule_hours(schedule_hours)

    for hour in hours:
        candidate = now.replace(
            hour=hour,
            minute=0,
            second=0,
            microsecond=0,
        )
        if candidate > now:
            return candidate

    next_day = now + timedelta(days=1)
    return next_day.replace(
        hour=hours[0],
        minute=0,
        second=0,
        microsecond=0,
    )


class RankingScheduler:
    """按上海时区触发采集函数，并防止同一时段重复执行。"""

    def __init__(
        self,
        collect_function,
        get_last_success_at=None,
        on_failure=None,
        schedule_hours=SCHEDULE_HOURS,
        scheduler_timezone=TIMEZONE,
    ):
        if not callable(collect_function):
            raise TypeError("collect_function 必须是可调用函数")
        if get_last_success_at is not None and not callable(
            get_last_success_at
        ):
            raise TypeError("get_last_success_at 必须是可调用函数")
        if on_failure is not None and not callable(on_failure):
            raise TypeError("on_failure 必须是可调用函数")

        self.collect_function = collect_function
        self.get_last_success_at = get_last_success_at
        self.on_failure = on_failure
        if not isinstance(scheduler_timezone, tzinfo):
            raise TypeError("scheduler_timezone 必须是时区对象")

        self.schedule_hours = validate_schedule_hours(schedule_hours)
        self.timezone = scheduler_timezone

        self.last_attempted_schedule = None
        self.run_lock = threading.Lock()

    def get_now(self):
        """获取上海时区的当前时间。"""
        return datetime.now(self.timezone)

    def normalize_time(self, value, field_name):
        """校验时间并转换到调度器时区。"""
        validate_datetime(value, field_name)
        return value.astimezone(self.timezone)

    def needs_collection(self, scheduled_at):
        """判断指定计划时段是否已经成功采集。"""
        if self.last_attempted_schedule == scheduled_at:
            return False

        if self.get_last_success_at is None:
            return True

        last_success_at = self.get_last_success_at()
        if last_success_at is None:
            return True

        last_success_at = self.normalize_time(
            last_success_at,
            "last_success_at",
        )
        return last_success_at < scheduled_at

    def execute_collection(self, scheduled_at):
        """执行一次采集；已有任务运行时直接跳过。"""
        scheduled_at = self.normalize_time(
            scheduled_at,
            "scheduled_at",
        )

        if not self.run_lock.acquire(blocking=False):
            logger.warning("采集任务仍在运行，跳过计划时间 %s", scheduled_at)
            return False

        self.last_attempted_schedule = scheduled_at

        try:
            logger.info("开始执行计划时间 %s 的采集任务", scheduled_at)
            self.collect_function(scheduled_at)
            logger.info("计划时间 %s 的采集任务执行完成", scheduled_at)
            return True
        except Exception as error:
            logger.exception("计划时间 %s 的采集任务失败", scheduled_at)

            if self.on_failure is not None:
                try:
                    self.on_failure(scheduled_at, error)
                except Exception:
                    logger.exception("记录采集失败信息时再次发生异常")

            return False
        finally:
            self.run_lock.release()

    def run_pending(self, now=None):
        """检查最近计划时段，必要时立即执行或补采一次。"""
        if now is None:
            now = self.get_now()
        else:
            now = self.normalize_time(now, "now")

        scheduled_at = get_latest_schedule_time(
            now,
            self.schedule_hours,
        )
        if not self.needs_collection(scheduled_at):
            return False

        return self.execute_collection(scheduled_at)

    def run_forever(self, stop_event=None, poll_seconds=30):
        """持续运行调度器，启动时会先检查是否需要补采。"""
        if (
            not isinstance(poll_seconds, (int, float))
            or isinstance(poll_seconds, bool)
        ):
            raise TypeError("poll_seconds 必须是数字")
        if poll_seconds <= 0:
            raise ValueError("poll_seconds 必须大于 0")

        if stop_event is None:
            stop_event = threading.Event()
        if not hasattr(stop_event, "wait") or not hasattr(
            stop_event,
            "is_set",
        ):
            raise TypeError("stop_event 必须提供 wait 和 is_set 方法")

        logger.info(
            "Ranking Scheduler 已启动，时区=%s，执行小时=%s",
            self.timezone,
            self.schedule_hours,
        )

        while not stop_event.is_set():
            self.run_pending()
            stop_event.wait(poll_seconds)


def run_scheduler(
    collect_function,
    get_last_success_at=None,
    on_failure=None,
    stop_event=None,
):
    """创建并持续运行默认调度器。"""
    scheduler = RankingScheduler(
        collect_function=collect_function,
        get_last_success_at=get_last_success_at,
        on_failure=on_failure,
    )
    scheduler.run_forever(stop_event=stop_event)


__all__ = [
    "RankingScheduler",
    "get_latest_schedule_time",
    "get_next_schedule_time",
    "run_scheduler",
    "validate_schedule_hours",
]
