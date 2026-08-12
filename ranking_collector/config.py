#!/usr/bin/env python 3.12
# -*- coding: utf-8 -*-
# time: 2026/07/24
# name: Haowen Cui

from datetime import timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DATABASE_PATH = BASE_DIR / "data" / "ranking.db"

API_URL = "https://api.bilibili.com/x/web-interface/ranking/v2"

TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")

SCHEDULE_HOURS = (0, 6, 12, 18)

TOP_N = 100

TURNOVER_MAX_HOURS = 24

PARTITIONS = {
    "all": {
        "name": "全站",
        "rid": 0,
        "enabled": True,
    },
    "knowledge": {
        "name": "知识",
        "rid": 36,
        "enabled": True,
    },
    "tech": {
        "name": "科技",
        "rid": 188,
        "enabled": True,
    },
    "game": {
        "name": "游戏",
        "rid": 4,
        "enabled": True,
    },
    "life": {
        "name": "生活",
        "rid": 160,
        "enabled": True,
    },
}
