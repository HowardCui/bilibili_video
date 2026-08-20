#!/usr/bin/env python 3.12

"""Parse public danmaku XML and build identity-free word-frequency data."""

import json
import re
import unicodedata
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

BASE_DIR = Path(__file__).resolve().parent.parent
DANMAKU_CACHE_DIR = BASE_DIR / "data" / "danmaku_word_clouds"
MAX_DANMAKU_COMMENTS = 20_000
MAX_WORDS = 120
MAX_XML_BYTES = 32 * 1024 * 1024
CACHE_MAX_AGE_HOURS = 168

DEFAULT_STOP_WORDS = frozenset(
    {
        "一个", "一些", "不是", "什么", "这个", "那个", "真的", "就是",
        "可以", "没有", "还是", "然后", "但是", "因为", "所以", "已经",
        "我们", "你们", "他们", "自己", "感觉", "看到", "视频", "哈哈",
        "哈哈哈", "啊啊", "啊啊啊", "了", "的", "是", "在", "和", "有",
        "也", "都", "就", "我", "你", "他", "她", "它", "吗", "呢", "吧",
    }
)

_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_BVID = re.compile(r"\bBV[0-9A-Za-z]+\b", re.IGNORECASE)
_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_+-]{1,}|[\u3400-\u9fff]{2,}")
_ONLY_DIGITS = re.compile(r"^\d+$")


def _positive_integer(value, field_name):
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{field_name} 必须是整数")
    if value < 1:
        raise ValueError(f"{field_name} 必须大于 0")
    return value


def parse_danmaku_xml(path, max_comments=MAX_DANMAKU_COMMENTS):
    """Read public display fields from ``<d>`` nodes without user identity."""
    max_comments = _positive_integer(max_comments, "max_comments")
    xml_path = Path(path)
    if not xml_path.is_file():
        raise FileNotFoundError(f"弹幕文件不存在：{xml_path}")
    if xml_path.stat().st_size > MAX_XML_BYTES:
        raise ValueError("弹幕 XML 超过允许大小")

    comments = []
    try:
        for _event, element in ElementTree.iterparse(xml_path, events=("end",)):
            if element.tag != "d":
                element.clear()
                continue
            values = (element.get("p") or "").split(",")
            if len(values) >= 4 and element.text:
                try:
                    comments.append(
                        {
                            "time": float(values[0]),
                            "mode": int(values[1]),
                            "font_size": int(values[2]),
                            "color": int(values[3]),
                            "text": element.text,
                        }
                    )
                except (TypeError, ValueError):
                    pass
            element.clear()
            if len(comments) >= max_comments:
                break
    except ElementTree.ParseError as error:
        raise ValueError("弹幕 XML 格式无效") from error
    return comments


def _default_tokenizer(text):
    try:
        import jieba
    except ImportError:
        return _TOKEN.findall(text)
    return jieba.lcut(text, cut_all=False)


def _normalize_text(value):
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = _URL.sub(" ", text)
    text = _BVID.sub(" ", text)
    return " ".join(text.split()).strip()


def _clean_tokens(text, tokenizer, stop_words):
    tokens = []
    seen = set()
    for raw_token in tokenizer(text):
        token = unicodedata.normalize("NFKC", str(raw_token)).strip().lower()
        token = "".join(_TOKEN.findall(token))
        if (
            len(token) < 2
            or token in stop_words
            or _ONLY_DIGITS.fullmatch(token)
            or token in seen
        ):
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def build_word_cloud(
    video_id,
    comments,
    tokenizer=None,
    stop_words=DEFAULT_STOP_WORDS,
    max_words=MAX_WORDS,
):
    """Build deterministic word frequencies after spam and noise filtering."""
    if not isinstance(video_id, str) or not video_id.strip():
        raise ValueError("video_id 不能为空")
    if not isinstance(comments, list):
        raise TypeError("comments 必须是列表")
    max_words = _positive_integer(max_words, "max_words")
    tokenizer = tokenizer or _default_tokenizer
    stop_words = {str(word).strip().lower() for word in stop_words}

    counts = Counter()
    first_seen = {}
    seen_comments = set()
    used_comments = 0
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        text = _normalize_text(comment.get("text"))
        if not text or text in seen_comments:
            continue
        tokens = _clean_tokens(text, tokenizer, stop_words)
        if not tokens:
            continue
        seen_comments.add(text)
        used_comments += 1
        for token in tokens:
            first_seen.setdefault(token, len(first_seen))
            counts[token] += 1

    words = [
        {"text": word, "count": count}
        for word, count in sorted(
            counts.items(),
            key=lambda item: (-item[1], first_seen[item[0]], item[0]),
        )[:max_words]
    ]
    return {
        "video_id": video_id,
        "status": "AVAILABLE" if words else "EMPTY",
        "collected_at": datetime.now(UTC).isoformat(),
        "total_comments": len(comments),
        "used_comments": used_comments,
        "words": words,
    }


def unavailable_word_cloud(video_id, reason):
    """Return a safe non-fatal state without exposing exception details."""
    return {
        "video_id": video_id,
        "status": "UNAVAILABLE",
        "reason": reason,
        "total_comments": 0,
        "used_comments": 0,
        "words": [],
    }


def save_word_cloud_cache(payload, path):
    """Persist identity-free word frequencies as UTF-8 JSON."""
    if not isinstance(payload, dict):
        raise TypeError("payload 必须是字典")
    cache_path = Path(path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    return cache_path


def load_word_cloud_cache(path, max_age_hours=None):
    """Load a valid cached word-cloud result, or return ``None``."""
    cache_path = Path(path)
    if not cache_path.is_file():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("words"), list):
        return None
    if max_age_hours is not None:
        if (
            not isinstance(max_age_hours, (int, float))
            or isinstance(max_age_hours, bool)
            or max_age_hours <= 0
        ):
            raise ValueError("max_age_hours 必须大于 0")
        try:
            collected_at = datetime.fromisoformat(payload["collected_at"])
        except (KeyError, TypeError, ValueError):
            return None
        if collected_at.tzinfo is None or collected_at.utcoffset() is None:
            return None
        age_hours = (
            datetime.now(UTC) - collected_at.astimezone(UTC)
        ).total_seconds() / 3600
        if age_hours > max_age_hours:
            return None
    return payload


__all__ = [
    "DANMAKU_CACHE_DIR",
    "CACHE_MAX_AGE_HOURS",
    "MAX_DANMAKU_COMMENTS",
    "build_word_cloud",
    "load_word_cloud_cache",
    "parse_danmaku_xml",
    "save_word_cloud_cache",
    "unavailable_word_cloud",
]
