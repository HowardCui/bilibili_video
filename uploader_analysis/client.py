"""独立的 Bilibili UP 投稿客户端。"""

import hashlib
import time
from pathlib import PurePosixPath
from urllib.parse import urlencode, urlparse

from curl_cffi.requests.exceptions import RequestException

from ranking_collector.client import get_cookie_session, get_shared_session

NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
UPLOADER_VIDEO_URL = "https://api.bilibili.com/x/space/wbi/arc/search"
MIXIN_KEY_ORDER = (
    46,
    47,
    18,
    2,
    53,
    8,
    23,
    32,
    15,
    50,
    10,
    31,
    58,
    3,
    45,
    35,
    27,
    43,
    5,
    49,
    33,
    9,
    42,
    19,
    29,
    28,
    14,
    39,
    12,
    38,
    41,
    13,
    37,
    48,
    7,
    16,
    24,
    55,
    40,
    61,
    26,
    17,
    0,
    1,
    60,
    51,
    30,
    4,
    22,
    25,
    54,
    21,
    56,
    59,
    6,
    63,
    57,
    62,
    11,
    36,
    20,
    34,
    44,
    52,
)


class UploaderClientError(RuntimeError):
    def __init__(
        self,
        message,
        error_code="REQUEST_FAILED",
        http_status=None,
    ):
        super().__init__(message)
        self.error_code = error_code
        self.http_status = http_status


def _request_error(error, request_stage=None):
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    if status == 412:
        code = (
            f"{request_stage}_RISK_CONTROL"
            if request_stage is not None
            else "RISK_CONTROL"
        )
        return UploaderClientError("Bilibili 请求触发风控", code, status)
    return UploaderClientError("UP 历史投稿请求失败", http_status=status)


def _mixin_key(image_key, sub_key):
    source = image_key + sub_key
    return "".join(source[index] for index in MIXIN_KEY_ORDER)[:32]


def build_wbi_params(params, image_key, sub_key, timestamp=None):
    values = {
        key: str(value).translate(str.maketrans("", "", "!'()*"))
        for key, value in params.items()
    }
    values["wts"] = int(timestamp if timestamp is not None else time.time())
    query = urlencode(sorted(values.items()))
    values["w_rid"] = hashlib.md5(
        (query + _mixin_key(image_key, sub_key)).encode()
    ).hexdigest()
    return values


def _key_from_url(value):
    return PurePosixPath(urlparse(value).path).stem


def get_wbi_keys(session, timeout=15, request_stage=None):
    try:
        response = session.get(NAV_URL, impersonate="firefox", timeout=timeout)
        response.raise_for_status()
        images = (response.json().get("data") or {}).get("wbi_img") or {}
        return _key_from_url(images["img_url"]), _key_from_url(images["sub_url"])
    except RequestException as error:
        raise _request_error(error, request_stage) from error
    except (ValueError, KeyError, TypeError) as error:
        raise UploaderClientError("无法获取 UP 投稿请求签名") from error


def _fetch_with_session(
    session,
    uploader_id,
    page,
    page_size,
    timeout,
    wbi_keys,
    timestamp,
    request_stage=None,
):
    image_key, sub_key = wbi_keys or get_wbi_keys(
        session, timeout, request_stage
    )
    params = build_wbi_params(
        {
            "mid": uploader_id,
            "pn": page,
            "ps": page_size,
            "order": "pubdate",
            "tid": 0,
            "keyword": "",
        },
        image_key,
        sub_key,
        timestamp,
    )
    try:
        response = session.get(
            f"{UPLOADER_VIDEO_URL}?{urlencode(params)}",
            impersonate="firefox",
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except RequestException as error:
        raise _request_error(error, request_stage) from error
    except ValueError as error:
        raise UploaderClientError("UP 历史投稿响应无效") from error
    if not isinstance(payload, dict):
        raise UploaderClientError("UP 历史投稿响应无效")
    code = payload.get("code")
    if code != 0:
        codes = {-352: "RISK_CONTROL", -404: "NOT_FOUND", -403: "RESTRICTED"}
        error_code = codes.get(code, "API_ERROR")
        if error_code == "RISK_CONTROL" and request_stage is not None:
            error_code = f"{request_stage}_RISK_CONTROL"
        raise UploaderClientError("UP 历史投稿暂时不可用", error_code)
    data = payload.get("data") or {}
    videos = (data.get("list") or {}).get("vlist") or []
    page_data = data.get("page") or {}
    total = int(page_data.get("count") or len(videos))
    current_page = int(page_data.get("pn") or page)
    size = int(page_data.get("ps") or page_size)
    return {
        "videos": videos,
        "next_cursor": current_page + 1,
        "has_more": current_page * size < total,
        "total": total,
    }


def fetch_uploader_page(
    uploader_id,
    page=1,
    page_size=30,
    timeout=15,
    session=None,
    wbi_keys=None,
    timestamp=None,
):
    if not isinstance(uploader_id, int) or isinstance(uploader_id, bool):
        raise TypeError("uploader_id 必须是整数")
    if uploader_id < 1 or page < 1 or not 1 <= page_size <= 50:
        raise ValueError("UP UID、页码和每页数量必须在有效范围内")
    if session is not None:
        return _fetch_with_session(
            session, uploader_id, page, page_size, timeout, wbi_keys, timestamp
        )
    try:
        return _fetch_with_session(
            get_shared_session(),
            uploader_id,
            page,
            page_size,
            timeout,
            wbi_keys,
            timestamp,
            "ANONYMOUS",
        )
    except UploaderClientError:
        cookie_session = get_cookie_session()
        if cookie_session is None:
            raise
        return _fetch_with_session(
            cookie_session,
            uploader_id,
            page,
            page_size,
            timeout,
            wbi_keys,
            timestamp,
            "COOKIE",
        )


__all__ = ["UploaderClientError", "build_wbi_params", "fetch_uploader_page"]
