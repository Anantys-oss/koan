"""Tests for authoritative_usage.py — source selection + interpolation.

Covers: config flag values, provider gating (Claude vs non-Claude / no-quota),
poll caching, staleness ceiling, per-window reset fallback, and local-counter
interpolation on top of the authoritative anchor.
"""

import json
from dataclasses import dataclass
from typing import Optional
from unittest.mock import Mock, patch

import pytest

from app import authoritative_usage as au


@dataclass
class _FakeUsage:
    """Stand-in for oauth_usage.OAuthUsage with the attributes maybe_poll reads."""

    session_pct: Optional[float] = None
    weekly_pct: Optional[float] = None
    session_resets_at: Optional[int] = None
    weekly_resets_at: Optional[int] = None


# --- config flag ------------------------------------------------------------


@pytest.mark.parametrize("value,expected", [
    ("auto", "auto"),
    ("oauth_usage", "oauth_usage"),
    ("off", "off"),
    ("OFF", "off"),
    ("nonsense", "auto"),
    (None, "auto"),
])
def test_config_source(value, expected):
    cfg = {"usage": {"authoritative_source": value}} if value is not None else {}
    assert au.config_source(cfg) == expected


def test_config_source_missing_usage():
    assert au.config_source({}) == "auto"
    assert au.config_source({"usage": "bad"}) == "auto"


# --- provider gating / is_enabled ------------------------------------------


def _patch_provider(name, has_quota=True):
    prov = Mock()
    prov.name = name
    prov.has_api_quota.return_value = has_quota
    return patch("app.provider.get_provider", return_value=prov)


def test_is_enabled_off_flag():
    with _patch_provider("claude"):
        assert au.is_enabled({"usage": {"authoritative_source": "off"}}) is False


def test_is_enabled_auto_claude():
    with _patch_provider("claude"):
        assert au.is_enabled({"usage": {"authoritative_source": "auto"}}) is True


def test_is_enabled_oauth_usage_claude():
    with _patch_provider("claude"):
        assert au.is_enabled({"usage": {"authoritative_source": "oauth_usage"}}) is True


def test_is_enabled_non_claude_provider():
    with _patch_provider("codex"):
        assert au.is_enabled({"usage": {"authoritative_source": "auto"}}) is False


def test_is_enabled_claude_without_quota():
    with _patch_provider("claude", has_quota=False):
        assert au.is_enabled({"usage": {"authoritative_source": "auto"}}) is False


def test_is_enabled_provider_error_degrades():
    with patch("app.provider.get_provider", side_effect=RuntimeError("boom")):
        assert au.is_enabled({"usage": {"authoritative_source": "auto"}}) is False


# --- maybe_poll -------------------------------------------------------------


def test_maybe_poll_disabled_returns_none(tmp_path):
    with patch.object(au, "is_enabled", return_value=False):
        assert au.maybe_poll(tmp_path, {}, {}, fetch=Mock()) is None


def test_maybe_poll_fresh_cache_skips_fetch(tmp_path):
    anchor = au.Anchor(session_pct=40, weekly_pct=50, session_resets_at=None,
                       weekly_resets_at=None, polled_at=1000,
                       session_tokens_at_poll=0, weekly_tokens_at_poll=0)
    au._save_anchor(tmp_path, anchor)
    fetch = Mock()
    with patch.object(au, "is_enabled", return_value=True):
        # now only 10s after the poll (< default 300s interval)
        result = au.maybe_poll(tmp_path, {}, {}, now=1010, fetch=fetch)
    assert result.session_pct == 40
    fetch.assert_not_called()


def test_maybe_poll_stale_cache_fetches_and_saves(tmp_path):
    stale = au.Anchor(session_pct=10, weekly_pct=10, session_resets_at=None,
                      weekly_resets_at=None, polled_at=1000,
                      session_tokens_at_poll=0, weekly_tokens_at_poll=0)
    au._save_anchor(tmp_path, stale)
    fetch = Mock(return_value=_FakeUsage(session_pct=70, weekly_pct=80,
                                         session_resets_at=99999,
                                         weekly_resets_at=88888))
    state = {"session_tokens": 12345, "weekly_tokens": 67890}
    with patch.object(au, "is_enabled", return_value=True):
        result = au.maybe_poll(tmp_path, {}, state, now=1000 + 400, fetch=fetch)
    fetch.assert_called_once()
    assert result.session_pct == 70
    assert result.session_tokens_at_poll == 12345
    assert result.weekly_tokens_at_poll == 67890
    # Persisted to disk.
    saved = json.loads((tmp_path / au.CACHE_FILE).read_text())
    assert saved["session_pct"] == 70


def test_maybe_poll_fetch_failure_keeps_old_anchor(tmp_path):
    old = au.Anchor(session_pct=15, weekly_pct=15, session_resets_at=None,
                    weekly_resets_at=None, polled_at=1000,
                    session_tokens_at_poll=0, weekly_tokens_at_poll=0)
    au._save_anchor(tmp_path, old)
    fetch = Mock(return_value=None)  # endpoint failed
    with patch.object(au, "is_enabled", return_value=True):
        result = au.maybe_poll(tmp_path, {}, {}, now=1000 + 400, fetch=fetch)
    assert result.session_pct == 15  # unchanged old anchor


def test_maybe_poll_fetch_raises_keeps_old_anchor(tmp_path):
    old = au.Anchor(session_pct=22, weekly_pct=22, session_resets_at=None,
                    weekly_resets_at=None, polled_at=1000,
                    session_tokens_at_poll=0, weekly_tokens_at_poll=0)
    au._save_anchor(tmp_path, old)
    fetch = Mock(side_effect=RuntimeError("network"))
    with patch.object(au, "is_enabled", return_value=True):
        result = au.maybe_poll(tmp_path, {}, {}, now=1000 + 400, fetch=fetch)
    assert result.session_pct == 22


# --- resolve ----------------------------------------------------------------


def _cfg(**usage):
    return {"usage": {"session_token_limit": 500_000,
                      "weekly_token_limit": 5_000_000, **usage}}


def test_resolve_disabled_returns_heuristic(tmp_path):
    with patch.object(au, "is_enabled", return_value=False):
        res = au.resolve(instance_dir=tmp_path, config={}, state={},
                         heuristic_session_pct=33, heuristic_weekly_pct=44,
                         session_reset_display="3h", weekly_reset_display="2d")
    assert res.source == "heuristic"
    assert res.session_pct == 33
    assert res.weekly_pct == 44


def test_resolve_authoritative_with_interpolation(tmp_path):
    # anchor: 40% session at 0 tokens; now 50k tokens of a 500k limit → +10%.
    anchor = au.Anchor(session_pct=40, weekly_pct=20,
                       session_resets_at=2000, weekly_resets_at=200000,
                       polled_at=1000, session_tokens_at_poll=0,
                       weekly_tokens_at_poll=0)
    state = {"session_tokens": 50_000, "weekly_tokens": 0}
    with patch.object(au, "is_enabled", return_value=True), \
         patch.object(au, "maybe_poll", return_value=anchor):
        res = au.resolve(instance_dir=tmp_path, config=_cfg(), state=state,
                         heuristic_session_pct=99, heuristic_weekly_pct=99,
                         session_reset_display="h-sess", weekly_reset_display="h-wk",
                         now=1100)
    assert res.source == "oauth_usage"
    assert res.session_pct == pytest.approx(50.0)  # 40 + 10 interpolated
    assert res.weekly_pct == pytest.approx(20.0)   # no weekly tokens since poll
    # Reset display derived from resets_at (2000 - 1100 = 900s = 15m).
    assert res.session_reset_display == "15m"


def test_resolve_stale_anchor_falls_back(tmp_path):
    anchor = au.Anchor(session_pct=40, weekly_pct=20, session_resets_at=None,
                       weekly_resets_at=None, polled_at=1000,
                       session_tokens_at_poll=0, weekly_tokens_at_poll=0)
    with patch.object(au, "is_enabled", return_value=True), \
         patch.object(au, "maybe_poll", return_value=anchor):
        # now far past polled_at + max staleness (default 900s)
        res = au.resolve(instance_dir=tmp_path, config=_cfg(), state={},
                         heuristic_session_pct=11, heuristic_weekly_pct=12,
                         session_reset_display="a", weekly_reset_display="b",
                         now=1000 + 5000)
    assert res.source == "heuristic"
    assert res.session_pct == 11


def test_resolve_window_already_reset_uses_heuristic(tmp_path):
    # session window reset (now >= session_resets_at) but weekly still valid.
    anchor = au.Anchor(session_pct=40, weekly_pct=25,
                       session_resets_at=1050, weekly_resets_at=999999,
                       polled_at=1000, session_tokens_at_poll=0,
                       weekly_tokens_at_poll=0)
    with patch.object(au, "is_enabled", return_value=True), \
         patch.object(au, "maybe_poll", return_value=anchor):
        res = au.resolve(instance_dir=tmp_path, config=_cfg(), state={},
                         heuristic_session_pct=7, heuristic_weekly_pct=8,
                         session_reset_display="hs", weekly_reset_display="hw",
                         now=1100)  # past session reset (1050), within staleness
    # Weekly still authoritative → overall source oauth_usage.
    assert res.source == "oauth_usage"
    assert res.session_pct == 7             # heuristic (window reset)
    assert res.session_reset_display == "hs"
    assert res.weekly_pct == pytest.approx(25.0)


def test_resolve_both_windows_reset_returns_heuristic(tmp_path):
    anchor = au.Anchor(session_pct=40, weekly_pct=25,
                       session_resets_at=1050, weekly_resets_at=1050,
                       polled_at=1000, session_tokens_at_poll=0,
                       weekly_tokens_at_poll=0)
    with patch.object(au, "is_enabled", return_value=True), \
         patch.object(au, "maybe_poll", return_value=anchor):
        res = au.resolve(instance_dir=tmp_path, config=_cfg(), state={},
                         heuristic_session_pct=7, heuristic_weekly_pct=8,
                         session_reset_display="hs", weekly_reset_display="hw",
                         now=1100)
    assert res.source == "heuristic"


def test_resolve_no_anchor_returns_heuristic(tmp_path):
    with patch.object(au, "is_enabled", return_value=True), \
         patch.object(au, "maybe_poll", return_value=None):
        res = au.resolve(instance_dir=tmp_path, config=_cfg(), state={},
                         heuristic_session_pct=5, heuristic_weekly_pct=6,
                         session_reset_display="a", weekly_reset_display="b")
    assert res.source == "heuristic"


def test_resolve_interpolation_clamps_at_100(tmp_path):
    anchor = au.Anchor(session_pct=95, weekly_pct=0, session_resets_at=999999,
                       weekly_resets_at=999999, polled_at=1000,
                       session_tokens_at_poll=0, weekly_tokens_at_poll=0)
    state = {"session_tokens": 500_000}  # +100% raw → clamps
    with patch.object(au, "is_enabled", return_value=True), \
         patch.object(au, "maybe_poll", return_value=anchor):
        res = au.resolve(instance_dir=tmp_path, config=_cfg(), state=state,
                         heuristic_session_pct=0, heuristic_weekly_pct=0,
                         session_reset_display="a", weekly_reset_display="b",
                         now=1001)
    assert res.session_pct == 100.0


# --- reset formatting -------------------------------------------------------


@pytest.mark.parametrize("remaining,expected", [
    (0, "0m"),
    (-5, "0m"),
    (600, "10m"),
    (3600, "1h00m"),
    (3600 + 15 * 60, "1h15m"),
    (86400, "1d"),
    (86400 + 3 * 3600, "1d3h"),
])
def test_format_reset(remaining, expected):
    assert au._format_reset(1000 + remaining, 1000) == expected
