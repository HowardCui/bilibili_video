#!/usr/bin/env python 3.12
# -*- coding: utf-8 -*-
# time: 2026/07/24
# name: Haowen Cui

"""Bilibili 热门排行榜接口客户端。"""

import os
import random
import time
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from urllib.parse import urlencode

from curl_cffi import requests
from curl_cffi.requests.exceptions import RequestException

from app_logging import get_logger, log_event
from ranking_collector.config import API_URL, BASE_DIR, TOP_N

DEFAULT_TIMEOUT = 15
MAX_RISK_RETRIES = 1
RISK_COOLDOWN_MIN_SECONDS = 30
RISK_COOLDOWN_MAX_SECONDS = 60
POPULAR_API_URL = "https://api.bilibili.com/x/web-interface/popular"
DEFAULT_COOKIE_FILE = BASE_DIR / ".secrets" / "bilibili_cookies.txt"

REQUEST_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": (
        "zh-CN,zh;q=0.9,zh-TW;q=0.8,"
        "zh-HK;q=0.7,en-US;q=0.6,en;q=0.5"
    ),
    "Origin": "https://www.bilibili.com",
    "Referer": "https://www.bilibili.com/v/popular/rank/all",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}

_shared_session = None
_cookie_session = None
LOGGER = get_logger("ranking.client")


class RankingClientError(RuntimeError):
    """排行榜请求或响应处理失败。"""


def load_cookie_values(cookie_file):
    """从 Netscape 格式 Cookie 文件读取名称和值。"""
    path = Path(cookie_file)
    if not path.is_file():
        raise RankingClientError(f"Bilibili Cookie 文件不存在：{path}")

    cookie_jar = MozillaCookieJar(str(path))
    try:
        cookie_jar.load(ignore_discard=True, ignore_expires=True)
    except (OSError, ValueError) as error:
        raise RankingClientError(
            f"Bilibili Cookie 文件读取失败：{error}"
        ) from error

    return {cookie.name: cookie.value for cookie in cookie_jar}


def create_session(cookie_file=None):
    """创建 Bilibili 请求会话；仅显式传入路径时加载 Cookie。"""
    session = requests.Session()
    session.headers.update(REQUEST_HEADERS)

    if cookie_file:
        session.cookies.update(load_cookie_values(cookie_file))

    return session


def get_shared_session():
    """延迟创建进程内共享的匿名 Bilibili 请求会话。"""
    global _shared_session
    if _shared_session is None:
        _shared_session = create_session()
    return _shared_session


def get_cookie_file():
    """返回可用的 Cookie 文件；环境变量优先于项目默认位置。"""
    configured_file = os.getenv("BILIBILI_COOKIE_FILE")
    cookie_file = Path(configured_file) if configured_file else DEFAULT_COOKIE_FILE

    if cookie_file.is_file():
        return cookie_file
    if configured_file:
        raise RankingClientError(f"Bilibili Cookie 文件不存在：{cookie_file}")
    return None


def get_cookie_session():
    """仅在匿名请求失败后延迟创建带 Cookie 的共享会话。"""
    global _cookie_session
    if _cookie_session is None:
        cookie_file = get_cookie_file()
        if cookie_file is None:
            return None
        _cookie_session = create_session(cookie_file)
    return _cookie_session


def build_ranking_url(rid: int):
    """
    根据视频分区 ID 构造排行榜接口地址。

    :param rid: Bilibili 视频分区 ID
    :return: 完整排行榜接口地址
    """
    if not isinstance(rid, int) or isinstance(rid, bool):
        raise TypeError("rid 必须是整数")

    if rid < 0:
        raise ValueError("rid 不能小于 0")

    query = urlencode(
        {
            "rid": rid,
            "type": "all",
        }
    )

    return f"{API_URL}?{query}"


def _fetch_ranking_with_session(session, rid, limit, timeout):
    """
    获取指定分区的热门排行榜。

    :param rid: Bilibili 视频分区 ID，0 表示全站
    :param limit: 返回的视频数量
    :param timeout: HTTP 请求超时秒数
    :return: 排行榜视频原始数据列表
    """
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise TypeError("limit 必须是整数")

    if limit < 1:
        raise ValueError("limit 必须大于等于 1")

    if not isinstance(timeout, (int, float)):
        raise TypeError("timeout 必须是数字")

    if isinstance(timeout, bool) or timeout <= 0:
        raise ValueError("timeout 必须大于 0")

    if not hasattr(session, "get"):
        raise TypeError("session 必须提供 get 方法")

    request_urls = [build_ranking_url(rid)]

    if rid == 0:
        popular_query = urlencode({"pn": 1, "ps": max(limit, TOP_N)})
        request_urls.append(f"{POPULAR_API_URL}?{popular_query}")

    request_succeeded = False

    for request_index, request_url in enumerate(request_urls):
        for retry_count in range(MAX_RISK_RETRIES + 1):
            try:
                response = session.get(
                    request_url,
                    impersonate="firefox",
                    timeout=timeout,
                )
                response.raise_for_status()
            except RequestException as error:
                raise RankingClientError(
                    f"Bilibili 排行榜请求失败：{error}"
                ) from error

            try:
                payload = response.json()
            except ValueError as error:
                raise RankingClientError(
                    "Bilibili 排行榜返回了无效 JSON"
                ) from error

            if not isinstance(payload, dict):
                raise RankingClientError(
                    "Bilibili 排行榜响应不是 JSON 对象"
                )

            response_code = payload.get("code")

            if response_code == 0:
                request_succeeded = True
                break

            if (
                response_code == -352
                and retry_count < MAX_RISK_RETRIES
            ):
                wait_seconds = random.uniform(
                    RISK_COOLDOWN_MIN_SECONDS,
                    RISK_COOLDOWN_MAX_SECONDS,
                )
                time.sleep(wait_seconds)
                continue

            break

        if request_succeeded:
            break

        has_fallback = request_index + 1 < len(request_urls)
        if response_code == -352 and has_fallback:
            continue

        message = payload.get("message") or "未知错误"
        raise RankingClientError(
            f"Bilibili 排行榜接口错误："
            f"code={response_code}, message={message}"
        )

    data = payload.get("data")

    if not isinstance(data, dict):
        raise RankingClientError(
            "Bilibili 排行榜响应缺少 data"
        )

    items = data.get("list")

    if not isinstance(items, list):
        raise RankingClientError(
            "Bilibili 排行榜响应缺少 data.list"
        )

    return items[:limit]


def fetch_ranking(
    rid: int,
    limit: int = TOP_N,
    timeout: int = DEFAULT_TIMEOUT,
    session=None,
):
    """匿名采集排行榜；匿名失败时才使用本地 Cookie 重试。"""
    if session is not None:
        return _fetch_ranking_with_session(session, rid, limit, timeout)

    try:
        return _fetch_ranking_with_session(
            get_shared_session(),
            rid,
            limit,
            timeout,
        )
    except RankingClientError:
        cookie_session = get_cookie_session()
        if cookie_session is None:
            raise
        log_event(
            LOGGER,
            "WARNING",
            "ranking_cookie_fallback",
            "排行榜匿名请求失败，改用 Cookie 后备",
            task_type="ranking",
        )
        return _fetch_ranking_with_session(
            cookie_session,
            rid,
            limit,
            timeout,
        )


if __name__ == "__main__":
    ranking = fetch_ranking(rid=0)

    print(f"获取到 {len(ranking)} 个视频")

    if ranking:
        print(ranking[0].get("bvid"))
        print(ranking[0].get("title"))
