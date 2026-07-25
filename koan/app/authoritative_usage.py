"""Source selection between the authoritative OAuth usage endpoint and the
local heuristic estimator.

The authoritative snapshot **anchors** the local estimator; it does not replace
it. When a poll succeeds we store an *anchor*: the account-wide session/weekly
percentages, their real reset timestamps, and the local token-counter values at
the moment of the poll. Between polls the local token counter interpolates on
top of that anchor, so per-run attribution and burn-rate accounting stay on the
local counter (the OAuth figures are account-wide and cannot attribute a single
run).

Every failure path degrades to the heuristic:

- ``usage.authoritative_source: off`` → never used.
- Non-Claude provider or a provider without API quota → heuristic.
- No OAuth token / endpoint error → heuristic.
- Anchor older than the staleness ceiling, or its window already reset →
  heuristic (per window).

This module never changes ``decide_mode`` / ``burn_rate`` / ``quota_handler``;
it only influences the percentages and reset strings written into ``usage.md``
by :mod:`app.usage_estimator`, which the tracker then reads unchanged.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Optional, Tuple

from app.utils import atomic_write

logger = logging.getLogger(__name__)

SOURCE_AUTO = "auto"
SOURCE_OAUTH = "oauth_usage"
SOURCE_OFF = "off"
_VALID_SOURCES = (SOURCE_AUTO, SOURCE_OAUTH, SOURCE_OFF)

# How often to hit the endpoint. Polling is periodic, not per-run.
DEFAULT_POLL_SECONDS = 300
# Beyond this age with no fresh successful poll, stop trusting the anchor.
DEFAULT_MAX_STALENESS_SECONDS = 900

CACHE_FILE = ".oauth-usage.json"

# Source labels reported back to the caller.
RESOLVED_OAUTH = "oauth_usage"
RESOLVED_HEURISTIC = "heuristic"

# Mirror usage_estimator defaults so interpolation works even without config.
_DEFAULT_SESSION_LIMIT = 500_000
_DEFAULT_WEEKLY_LIMIT = 5_000_000


@dataclass(frozen=True)
class Anchor:
    """A successful authoritative poll plus the local counters at that moment.

    ``polled_at`` is the timestamp of the last *successful* fetch (drives data
    staleness in :func:`resolve`). ``last_attempt_at`` is the last *attempt* —
    success or failure — and drives the poll-interval short-circuit in
    :func:`maybe_poll` so a failing endpoint is negatively cached instead of
    re-hit on every mission. It defaults to ``polled_at`` for cache files
    written before this field existed.
    """

    session_pct: Optional[float]
    weekly_pct: Optional[float]
    session_resets_at: Optional[int]
    weekly_resets_at: Optional[int]
    polled_at: int
    session_tokens_at_poll: int
    weekly_tokens_at_poll: int
    last_attempt_at: Optional[int] = None

    @property
    def attempt_at(self) -> int:
        """Last attempt time, falling back to the last-success time."""
        return self.polled_at if self.last_attempt_at is None else self.last_attempt_at

    def has_data(self) -> bool:
        """True when this anchor carries usable authoritative percentages."""
        return self.session_pct is not None or self.weekly_pct is not None

    def to_dict(self) -> dict:
        return {
            "session_pct": self.session_pct,
            "weekly_pct": self.weekly_pct,
            "session_resets_at": self.session_resets_at,
            "weekly_resets_at": self.weekly_resets_at,
            "polled_at": self.polled_at,
            "session_tokens_at_poll": self.session_tokens_at_poll,
            "weekly_tokens_at_poll": self.weekly_tokens_at_poll,
            "last_attempt_at": self.attempt_at,
        }


@dataclass(frozen=True)
class UsageResolution:
    """Result of source selection: the values to write into ``usage.md``."""

    session_pct: float
    weekly_pct: float
    session_reset_display: str
    weekly_reset_display: str
    source: str  # RESOLVED_OAUTH or RESOLVED_HEURISTIC


# --- Config -----------------------------------------------------------------


def _usage_cfg(config: dict) -> dict:
    usage = config.get("usage", {}) if isinstance(config, dict) else {}
    return usage if isinstance(usage, dict) else {}


def config_source(config: dict) -> str:
    """Return the configured ``usage.authoritative_source`` (default auto)."""
    raw = str(_usage_cfg(config).get("authoritative_source", SOURCE_AUTO)).strip().lower()
    return raw if raw in _VALID_SOURCES else SOURCE_AUTO


def _poll_seconds(config: dict) -> int:
    try:
        return max(1, int(_usage_cfg(config).get("authoritative_poll_seconds",
                                                  DEFAULT_POLL_SECONDS)))
    except (TypeError, ValueError):
        return DEFAULT_POLL_SECONDS


def _max_staleness(config: dict) -> int:
    try:
        return max(1, int(_usage_cfg(config).get("authoritative_max_staleness_seconds",
                                                 DEFAULT_MAX_STALENESS_SECONDS)))
    except (TypeError, ValueError):
        return DEFAULT_MAX_STALENESS_SECONDS


def _limits(config: dict) -> Tuple[int, int]:
    usage = _usage_cfg(config)
    try:
        session = int(usage.get("session_token_limit", _DEFAULT_SESSION_LIMIT))
    except (TypeError, ValueError):
        session = _DEFAULT_SESSION_LIMIT
    try:
        weekly = int(usage.get("weekly_token_limit", _DEFAULT_WEEKLY_LIMIT))
    except (TypeError, ValueError):
        weekly = _DEFAULT_WEEKLY_LIMIT
    return session, weekly


def _provider_supports_oauth_usage() -> bool:
    """True only for a Claude provider that consumes metered API quota.

    Mandatory fallback surface: API-key users have no OAuth token (handled in
    :mod:`app.oauth_usage`), and non-Claude / local providers (Codex, Copilot,
    Ollama — ``has_api_quota()`` False) have no such endpoint at all.
    """
    try:
        from app.provider import get_provider

        provider = get_provider()
        return provider.name == "claude" and provider.has_api_quota()
    except Exception as exc:  # noqa: BLE001 — degrade to heuristic on any error
        logger.debug("provider probe for OAuth usage failed: %s", exc)
        return False


def is_enabled(config: dict) -> bool:
    """Whether authoritative usage should be attempted at all."""
    if config_source(config) == SOURCE_OFF:
        return False
    return _provider_supports_oauth_usage()


# --- Anchor cache -----------------------------------------------------------


def _cache_path(instance_dir: Path) -> Path:
    return Path(instance_dir) / CACHE_FILE


def _load_anchor(instance_dir: Path) -> Optional[Anchor]:
    path = _cache_path(instance_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return Anchor(
            session_pct=_opt_float(data.get("session_pct")),
            weekly_pct=_opt_float(data.get("weekly_pct")),
            session_resets_at=_opt_int(data.get("session_resets_at")),
            weekly_resets_at=_opt_int(data.get("weekly_resets_at")),
            polled_at=int(data["polled_at"]),
            session_tokens_at_poll=int(data.get("session_tokens_at_poll", 0)),
            weekly_tokens_at_poll=int(data.get("weekly_tokens_at_poll", 0)),
            last_attempt_at=_opt_int(data.get("last_attempt_at")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _save_anchor(instance_dir: Path, anchor: Anchor) -> None:
    try:
        atomic_write(_cache_path(instance_dir),
                     json.dumps(anchor.to_dict(), indent=2) + "\n")
    except OSError as exc:
        logger.debug("could not persist OAuth usage anchor: %s", exc)


def _opt_float(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _opt_int(value: object) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# --- Polling ----------------------------------------------------------------


def maybe_poll(
    instance_dir: Path,
    config: dict,
    state: dict,
    *,
    now: Optional[int] = None,
    fetch: Optional[Callable[[], object]] = None,
) -> Optional[Anchor]:
    """Return a usable anchor, polling the endpoint only when the cache is stale.

    The cached anchor is reused without any network call while its last
    *attempt* is younger than the poll interval. When it is older we attempt one
    fetch; on failure we persist the attempt time (negative cache) and keep any
    previous success data, so the staleness ceiling in :func:`resolve` gets the
    final say and a down endpoint is not re-hit on every mission.
    """
    if not is_enabled(config):
        return None
    now = int(time.time()) if now is None else now
    anchor = _load_anchor(instance_dir)
    # Gate on the last *attempt* (not just last success): a failed/rate-limited
    # poll is negatively cached for the same interval, so a down endpoint does
    # not re-trigger the synchronous, blocking fetch on every mission.
    if anchor is not None and (now - anchor.attempt_at) < _poll_seconds(config):
        return anchor

    fetcher = fetch or _default_fetch
    try:
        usage = fetcher()
    except Exception as exc:  # noqa: BLE001 — network path is best-effort
        logger.debug("OAuth usage poll raised: %s", exc)
        usage = None
    if usage is None:
        # Record the failed attempt so the interval short-circuit re-arms.
        # Previous success data (if any) is preserved; resolve() still judges
        # its freshness against polled_at.
        stale = _mark_attempt(anchor, now)
        _save_anchor(instance_dir, stale)
        return stale

    new_anchor = Anchor(
        session_pct=getattr(usage, "session_pct", None),
        weekly_pct=getattr(usage, "weekly_pct", None),
        session_resets_at=getattr(usage, "session_resets_at", None),
        weekly_resets_at=getattr(usage, "weekly_resets_at", None),
        polled_at=now,
        session_tokens_at_poll=_counter_or_zero(state, "session_tokens"),
        weekly_tokens_at_poll=_counter_or_zero(state, "weekly_tokens"),
        last_attempt_at=now,
    )
    _save_anchor(instance_dir, new_anchor)
    return new_anchor


def _mark_attempt(anchor: Optional[Anchor], now: int) -> Anchor:
    """Return an anchor whose ``last_attempt_at`` is *now*.

    With no prior anchor this is a pure negative-cache marker: no usable data
    and ``polled_at=0`` so :func:`resolve` treats it as stale (→ heuristic),
    while :func:`maybe_poll` still honours the poll interval before retrying.
    """
    if anchor is None:
        return Anchor(
            session_pct=None, weekly_pct=None,
            session_resets_at=None, weekly_resets_at=None,
            polled_at=0, session_tokens_at_poll=0, weekly_tokens_at_poll=0,
            last_attempt_at=now,
        )
    return replace(anchor, last_attempt_at=now)


def _default_fetch():
    from app import oauth_usage

    return oauth_usage.fetch_usage()


def _read_counter(state: dict, key: str) -> Optional[int]:
    """Read an integer token counter from state.

    Absent / empty → ``0``. A *present but unparseable* value returns ``None``
    and logs at WARNING: that signals counter corruption, and callers treat the
    window as un-interpolatable and fall back to the heuristic rather than
    silently interpolating from ``0`` (which under-reports usage — the
    dangerous direction, since the agent would believe it has more headroom
    than it does).
    """
    try:
        raw = state.get(key, 0)
    except AttributeError:
        return 0
    if raw is None or raw == "":
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("unparseable usage counter %s=%r; falling back to the "
                       "heuristic for this window", key, raw)
        return None


def _counter_or_zero(state: dict, key: str) -> int:
    """Counter value with a corrupt/missing reading coerced to 0.

    Used for the anchor *baseline* at poll time, where a later delta against a
    good counter over-reports (the safe direction) rather than under-reports.
    """
    value = _read_counter(state, key)
    return value if value is not None else 0


# --- Resolution -------------------------------------------------------------


def resolve(
    *,
    instance_dir: Path,
    config: dict,
    state: dict,
    heuristic_session_pct: float,
    heuristic_weekly_pct: float,
    session_reset_display: str,
    weekly_reset_display: str,
    now: Optional[int] = None,
    fetch: Optional[Callable[[], object]] = None,
) -> UsageResolution:
    """Prefer authoritative (anchor + local interpolation) when fresh.

    Falls back to the supplied heuristic values whenever authoritative data is
    disabled, unavailable, or stale. Session and weekly windows are judged
    independently: one may be authoritative while the other falls back.
    """
    heuristic = UsageResolution(
        session_pct=float(heuristic_session_pct),
        weekly_pct=float(heuristic_weekly_pct),
        session_reset_display=session_reset_display,
        weekly_reset_display=weekly_reset_display,
        source=RESOLVED_HEURISTIC,
    )
    if not is_enabled(config):
        return heuristic

    now = int(time.time()) if now is None else now
    anchor = maybe_poll(instance_dir, config, state, now=now, fetch=fetch)
    if anchor is None or (now - anchor.polled_at) > _max_staleness(config):
        return heuristic

    session_limit, weekly_limit = _limits(config)
    sess_pct, sess_reset, sess_ok = _resolve_window(
        anchor.session_pct, anchor.session_resets_at, anchor.session_tokens_at_poll,
        _read_counter(state, "session_tokens"), session_limit, now,
        heuristic_session_pct, session_reset_display,
    )
    week_pct, week_reset, week_ok = _resolve_window(
        anchor.weekly_pct, anchor.weekly_resets_at, anchor.weekly_tokens_at_poll,
        _read_counter(state, "weekly_tokens"), weekly_limit, now,
        heuristic_weekly_pct, weekly_reset_display,
    )
    if not sess_ok and not week_ok:
        return heuristic
    return UsageResolution(
        session_pct=sess_pct,
        weekly_pct=week_pct,
        session_reset_display=sess_reset,
        weekly_reset_display=week_reset,
        source=RESOLVED_OAUTH,
    )


def _resolve_window(
    anchor_pct: Optional[float],
    resets_at: Optional[int],
    tokens_at_poll: int,
    tokens_now: Optional[int],
    limit: int,
    now: int,
    heuristic_pct: float,
    heuristic_reset: str,
) -> Tuple[float, str, bool]:
    """Interpolate one window from its anchor, or fall back.

    Returns ``(pct, reset_display, used_authoritative)``.
    """
    # No authoritative percentage for this window → heuristic.
    if anchor_pct is None:
        return float(heuristic_pct), heuristic_reset, False
    # Corrupt local counter → can't interpolate safely; heuristic (which would
    # otherwise under-report by assuming zero consumption since the anchor).
    if tokens_now is None:
        return float(heuristic_pct), heuristic_reset, False
    # The window already reset since the poll → the anchor is stale for it.
    if resets_at is not None and now >= resets_at:
        return float(heuristic_pct), heuristic_reset, False

    delta_tokens = max(0, tokens_now - tokens_at_poll)
    interp = (delta_tokens / limit * 100.0) if limit > 0 else 0.0
    pct = max(0.0, min(100.0, anchor_pct + interp))
    reset = _format_reset(resets_at, now) if resets_at is not None else heuristic_reset
    return pct, reset, True


def _format_reset(resets_at: int, now: int) -> str:
    """Format a future reset timestamp as a compact human string."""
    remaining = resets_at - now
    if remaining <= 0:
        return "0m"
    days = remaining // 86400
    hours = (remaining % 86400) // 3600
    minutes = (remaining % 3600) // 60
    if days >= 1:
        return f"{days}d{hours}h" if hours else f"{days}d"
    if hours > 0:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m"
