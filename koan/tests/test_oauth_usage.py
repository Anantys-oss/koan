"""Tests for oauth_usage.py — Anthropic OAuth usage endpoint client.

All HTTP and credential reads are mocked; no network or keychain access.
"""

import http.client
import json
import urllib.error
from unittest.mock import Mock, patch

import pytest

from app import oauth_usage
from app.oauth_usage import (
    OAuthRateLimited,
    OAuthUnauthorized,
    OAuthUsage,
    OAuthUsageError,
    UsageWindow,
    _backoff_delay,
    _extract_access_token,
    _http_get_usage,
    _parse_reset_ts,
    _parse_window,
    fetch_usage,
    parse_usage_response,
    read_access_token,
)


# --- Credential reading -----------------------------------------------------


def test_extract_access_token_valid():
    data = {"claudeAiOauth": {"accessToken": "sk-tok-123", "refreshToken": "r"}}
    assert _extract_access_token(data) == "sk-tok-123"


@pytest.mark.parametrize("data", [
    {},
    {"claudeAiOauth": {}},
    {"claudeAiOauth": {"accessToken": ""}},
    {"claudeAiOauth": {"accessToken": "   "}},
    {"claudeAiOauth": "not-a-dict"},
    {"other": {"accessToken": "x"}},
    "not-a-dict",
    None,
])
def test_extract_access_token_invalid(data):
    assert _extract_access_token(data) is None


def test_read_token_from_file(tmp_path, monkeypatch):
    cred = tmp_path / ".claude" / ".credentials.json"
    cred.parent.mkdir(parents=True)
    cred.write_text(json.dumps({"claudeAiOauth": {"accessToken": "from-file"}}))
    monkeypatch.setattr(oauth_usage.Path, "home", lambda: tmp_path)
    assert read_access_token() == "from-file"


def test_read_token_missing_file_falls_through_to_keychain(tmp_path, monkeypatch):
    monkeypatch.setattr(oauth_usage.Path, "home", lambda: tmp_path)  # no file
    with patch.object(oauth_usage, "_read_token_from_keychain",
                      return_value="from-keychain"):
        assert read_access_token() == "from-keychain"


def test_read_token_file_wins_over_keychain(tmp_path, monkeypatch):
    cred = tmp_path / ".claude" / ".credentials.json"
    cred.parent.mkdir(parents=True)
    cred.write_text(json.dumps({"claudeAiOauth": {"accessToken": "file-tok"}}))
    monkeypatch.setattr(oauth_usage.Path, "home", lambda: tmp_path)
    with patch.object(oauth_usage, "_read_token_from_keychain",
                      return_value="kc") as kc:
        assert read_access_token() == "file-tok"
        kc.assert_not_called()


def test_keychain_skipped_off_darwin(monkeypatch):
    monkeypatch.setattr(oauth_usage.sys, "platform", "linux")
    assert oauth_usage._read_token_from_keychain() is None


def test_keychain_read_on_darwin(monkeypatch):
    monkeypatch.setattr(oauth_usage.sys, "platform", "darwin")
    blob = json.dumps({"claudeAiOauth": {"accessToken": "kc-tok"}})
    fake = Mock(returncode=0, stdout=blob, stderr="")
    with patch.object(oauth_usage.shutil, "which", return_value="/usr/bin/security"), \
         patch.object(oauth_usage.subprocess, "run", return_value=fake):
        assert oauth_usage._read_token_from_keychain() == "kc-tok"


def test_keychain_nonzero_returncode(monkeypatch):
    monkeypatch.setattr(oauth_usage.sys, "platform", "darwin")
    fake = Mock(returncode=44, stdout="", stderr="not found")
    with patch.object(oauth_usage.shutil, "which", return_value="/usr/bin/security"), \
         patch.object(oauth_usage.subprocess, "run", return_value=fake):
        assert oauth_usage._read_token_from_keychain() is None


# --- Response parsing -------------------------------------------------------


def test_parse_usage_response_maps_windows():
    data = {
        "five_hour": {"utilization": 45, "resets_at": 1000},
        "seven_day": {"utilization": 60, "resets_at": 2000},
        "seven_day_opus": {"utilization": 12, "resets_at": 3000},
        "seven_day_sonnet": {"utilization": 30, "resets_at": 4000},
    }
    usage = parse_usage_response(data, fetched_at=999)
    assert usage.session_pct == 45
    assert usage.weekly_pct == 60
    assert usage.session_resets_at == 1000
    assert usage.weekly_resets_at == 2000
    # Per-model buckets preserved verbatim.
    assert usage.windows["seven_day_opus"].percent == 12
    assert usage.windows["seven_day_sonnet"].resets_at == 4000
    assert usage.fetched_at == 999


def test_parse_usage_response_missing_windows():
    usage = parse_usage_response({}, fetched_at=1)
    assert usage.session_pct is None
    assert usage.weekly_pct is None
    assert usage.session_resets_at is None


@pytest.mark.parametrize("key", ["utilization", "percent", "used_pct", "usage"])
def test_parse_window_accepts_pct_spellings(key):
    win = _parse_window({key: 33, "resets_at": 5})
    assert win is not None
    assert win.percent == 33


def test_parse_window_clamps_percent():
    assert _parse_window({"utilization": 250}).percent == 100.0
    assert _parse_window({"utilization": -5}).percent == 0.0


def test_parse_window_no_percent_returns_none():
    assert _parse_window({"resets_at": 5}) is None
    assert _parse_window("not-a-dict") is None


def test_parse_reset_ts_variants():
    from datetime import datetime, timezone

    expected = int(datetime(2026, 7, 23, 18, 0, 0, tzinfo=timezone.utc).timestamp())
    assert _parse_reset_ts(1700000000) == 1700000000
    assert _parse_reset_ts("1700000000") == 1700000000
    assert _parse_reset_ts("2026-07-23T18:00:00+00:00") == expected
    assert _parse_reset_ts("2026-07-23T18:00:00Z") == expected
    assert _parse_reset_ts("garbage") is None
    assert _parse_reset_ts(None) is None
    assert _parse_reset_ts("") is None


# --- HTTP error mapping -----------------------------------------------------


def _http_error(code, retry_after=None):
    hdrs = http.client.HTTPMessage()
    if retry_after is not None:
        hdrs["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError("https://x", code, "err", hdrs, None)


def test_http_get_success():
    body = json.dumps({"five_hour": {"utilization": 10, "resets_at": 1}})

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return body.encode()

    with patch.object(oauth_usage.urllib.request, "urlopen", return_value=_Resp()):
        data = _http_get_usage("tok", timeout=1.0)
    assert data["five_hour"]["utilization"] == 10


def test_http_get_401_raises_unauthorized():
    with patch.object(oauth_usage.urllib.request, "urlopen",
                      side_effect=_http_error(401)):
        with pytest.raises(OAuthUnauthorized):
            _http_get_usage("tok", timeout=1.0)


def test_http_get_429_raises_rate_limited_with_retry_after():
    with patch.object(oauth_usage.urllib.request, "urlopen",
                      side_effect=_http_error(429, retry_after=17)):
        with pytest.raises(OAuthRateLimited) as exc:
            _http_get_usage("tok", timeout=1.0)
    assert exc.value.retry_after == 17.0


def test_http_get_500_raises_generic_error():
    with patch.object(oauth_usage.urllib.request, "urlopen",
                      side_effect=_http_error(500)):
        with pytest.raises(OAuthUsageError):
            _http_get_usage("tok", timeout=1.0)


def test_http_get_urlerror_raises_generic():
    with patch.object(oauth_usage.urllib.request, "urlopen",
                      side_effect=urllib.error.URLError("boom")):
        with pytest.raises(OAuthUsageError):
            _http_get_usage("tok", timeout=1.0)


# --- fetch_usage orchestration ---------------------------------------------


def test_fetch_usage_no_token_returns_none():
    with patch.object(oauth_usage, "read_access_token", return_value=None):
        assert fetch_usage() is None


def test_fetch_usage_success():
    usage_obj = OAuthUsage(windows={"five_hour": UsageWindow(10, 1)}, fetched_at=5)
    with patch.object(oauth_usage, "read_access_token", return_value="tok"), \
         patch.object(oauth_usage, "_http_get_usage", return_value={"raw": 1}), \
         patch.object(oauth_usage, "parse_usage_response", return_value=usage_obj):
        assert fetch_usage(sleep=Mock()) is usage_obj


def test_fetch_usage_401_rereads_token_once():
    tokens = iter(["stale-tok", "fresh-tok"])
    reader = Mock(side_effect=lambda: next(tokens))
    http_calls = [OAuthUnauthorized("401"), {"ok": 1}]
    parsed = OAuthUsage(windows={}, fetched_at=1)
    with patch.object(oauth_usage, "read_access_token", reader), \
         patch.object(oauth_usage, "_http_get_usage",
                      side_effect=http_calls), \
         patch.object(oauth_usage, "parse_usage_response", return_value=parsed):
        result = fetch_usage(sleep=Mock())
    assert result is parsed
    assert reader.call_count == 2  # initial + one re-read


def test_fetch_usage_401_same_token_gives_up():
    with patch.object(oauth_usage, "read_access_token", return_value="same"), \
         patch.object(oauth_usage, "_http_get_usage",
                      side_effect=OAuthUnauthorized("401")):
        assert fetch_usage(sleep=Mock()) is None


def test_fetch_usage_429_backoff_then_success():
    parsed = OAuthUsage(windows={}, fetched_at=1)
    sleep = Mock()
    with patch.object(oauth_usage, "read_access_token", return_value="tok"), \
         patch.object(oauth_usage, "_http_get_usage",
                      side_effect=[OAuthRateLimited(3.0), {"ok": 1}]), \
         patch.object(oauth_usage, "parse_usage_response", return_value=parsed):
        result = fetch_usage(sleep=sleep)
    assert result is parsed
    sleep.assert_called_once_with(3.0)


def test_fetch_usage_429_exhausts_retries():
    sleep = Mock()
    with patch.object(oauth_usage, "read_access_token", return_value="tok"), \
         patch.object(oauth_usage, "_http_get_usage",
                      side_effect=OAuthRateLimited(1.0)):
        assert fetch_usage(sleep=sleep, max_retries_429=2) is None
    assert sleep.call_count == 2


def test_fetch_usage_generic_error_returns_none():
    with patch.object(oauth_usage, "read_access_token", return_value="tok"), \
         patch.object(oauth_usage, "_http_get_usage",
                      side_effect=OAuthUsageError("500")):
        assert fetch_usage(sleep=Mock()) is None


def test_backoff_delay_prefers_retry_after():
    assert _backoff_delay(5.0, 1) == 5.0
    assert _backoff_delay(None, 1) == 1.0
    assert _backoff_delay(None, 3) == 4.0
    assert _backoff_delay(9999.0, 1) == oauth_usage._MAX_BACKOFF_SECONDS
