"""RankingClient Cookie 后备逻辑的最小函数测试。"""

import sys
from types import SimpleNamespace

try:
    import curl_cffi
except ImportError:
    fake_requests = SimpleNamespace(Session=lambda: None)
    sys.modules["curl_cffi"] = SimpleNamespace(requests=fake_requests)
    sys.modules["curl_cffi.requests"] = fake_requests
    sys.modules["curl_cffi.requests.exceptions"] = SimpleNamespace(
        RequestException=RuntimeError,
    )

import ranking_collector.client as client


def test_anonymous_success_does_not_load_cookie():
    original_fetch = client._fetch_ranking_with_session
    original_anonymous = client.get_shared_session
    original_cookie = client.get_cookie_session
    calls = []

    def get_anonymous():
        calls.append("anonymous_session")
        return "anonymous"

    def get_cookie():
        calls.append("cookie_session")
        return "cookie"

    def fetch(session, rid, limit, timeout):
        calls.append(session)
        return [{"bvid": "BV1"}]

    try:
        client.get_shared_session = get_anonymous
        client.get_cookie_session = get_cookie
        client._fetch_ranking_with_session = fetch
        result = client.fetch_ranking(0)
    finally:
        client.get_shared_session = original_anonymous
        client.get_cookie_session = original_cookie
        client._fetch_ranking_with_session = original_fetch

    assert result == [{"bvid": "BV1"}]
    assert calls == ["anonymous_session", "anonymous"]


def test_anonymous_failure_uses_cookie_session():
    original_fetch = client._fetch_ranking_with_session
    original_anonymous = client.get_shared_session
    original_cookie = client.get_cookie_session
    calls = []

    def get_anonymous():
        return "anonymous"

    def get_cookie():
        calls.append("cookie_session")
        return "cookie"

    def fetch(session, rid, limit, timeout):
        calls.append(session)
        if session == "anonymous":
            raise client.RankingClientError("anonymous failed")
        return [{"bvid": "BV2"}]

    try:
        client.get_shared_session = get_anonymous
        client.get_cookie_session = get_cookie
        client._fetch_ranking_with_session = fetch
        result = client.fetch_ranking(36)
    finally:
        client.get_shared_session = original_anonymous
        client.get_cookie_session = original_cookie
        client._fetch_ranking_with_session = original_fetch

    assert result == [{"bvid": "BV2"}]
    assert calls == ["anonymous", "cookie_session", "cookie"]


def run_all_tests():
    test_anonymous_success_does_not_load_cookie()
    test_anonymous_failure_uses_cookie_session()
    print("2 ranking client tests passed")


if __name__ == "__main__":
    run_all_tests()
