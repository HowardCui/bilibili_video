#!/usr/bin/env python 3.12
# -*- coding: utf-8 -*-
# time: 2026/07/24
# name: Haowen Cui

"""Bilibili 热门排行榜接口客户端。"""

from urllib.parse import urlencode

from curl_cffi import requests
from curl_cffi.requests.exceptions import RequestException

from ranking_collector.config import API_URL, TOP_N


DEFAULT_TIMEOUT = 15
POPULAR_API_URL = "https://api.bilibili.com/x/web-interface/popular"

REQUEST_HEADERS = {
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": (
        "zh-CN,zh;q=0.9,zh-TW;q=0.8,"
        "zh-HK;q=0.7,en-US;q=0.6,en;q=0.5"
    ),
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


class RankingClientError(RuntimeError):
    """排行榜请求或响应处理失败。"""


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


def fetch_ranking(rid: int,limit: int = TOP_N,timeout: int = DEFAULT_TIMEOUT,):
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

    request_urls = [build_ranking_url(rid)]

    if rid == 0:
        popular_query = urlencode({"pn": 1, "ps": max(limit, TOP_N)})
        request_urls.append(f"{POPULAR_API_URL}?{popular_query}")

    for request_index, request_url in enumerate(request_urls):
        try:
            response = requests.get(
                request_url,
                headers=REQUEST_HEADERS,
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


if __name__ == "__main__":
    ranking = fetch_ranking(rid=0)

    print(f"获取到 {len(ranking)} 个视频")

    if ranking:
        print(ranking[0].get("bvid"))
        print(ranking[0].get("title"))
