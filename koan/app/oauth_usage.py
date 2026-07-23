"""Anthropic OAuth usage endpoint client (best-effort, UNDOCUMENTED).

Reads the Claude Code CLI's stored OAuth *access* token and queries the
undocumented account usage endpoint to obtain authoritative session/weekly
utilization percentages and real reset timestamps.

CAVEATS — read before relying on this module:

- The endpoint ``GET https://api.anthropic.com/api/oauth/usage`` and the
  ``anthropic-beta: oauth-2025-04-20`` header are UNDOCUMENTED and UNSTABLE.
  Availability is best-effort: every failure path returns ``None`` so callers
  fall back to the local heuristic estimator (``usage_estimator``).
- Figures are ACCOUNT-WIDE, not per-run. Per-run attribution stays on Kōan's
  local token counter (``usage_estimator`` / ``burn_rate``); this module only
  supplies the anchor percentages that the local counter interpolates from.
- We only READ the CLI's access token; we never spend or rotate the refresh
  token. On HTTP 401 we re-read the (possibly CLI-refreshed) access token once
  and retry, rather than attempting a refresh ourselves.

The endpoint does not exist for API-key users (``ANTHROPIC_API_KEY``) or for
non-Claude providers, so :func:`fetch_usage` returns ``None`` whenever no OAuth
token can be read — the mandatory graceful-degradation path.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)

USAGE_ENDPOINT = "https://api.anthropic.com/api/oauth/usage"
OAUTH_BETA_HEADER = "oauth-2025-04-20"
# macOS keychain generic-password service name used by the Claude Code CLI.
KEYCHAIN_SERVICE = "Claude Code-credentials"
# JSON field holding the token blob in both the credentials file and keychain.
_OAUTH_FIELD = "claudeAiOauth"
_ACCESS_TOKEN_FIELD = "accessToken"

_DEFAULT_TIMEOUT = 10.0
_MAX_RETRIES_429 = 3
_MAX_BACKOFF_SECONDS = 60.0
_VERSION_PROBE_TIMEOUT = 5.0
_KEYCHAIN_TIMEOUT = 5.0
# User-Agent fallback when the CLI version cannot be probed. The endpoint is
# undocumented; the header shape (``claude-code/<version>``) matters more than
# the exact number.
_DEFAULT_CLI_VERSION = "0.0.0"

# Known usage-window keys in the response. ``five_hour`` maps to the session
# window; ``seven_day`` maps to the weekly window. The per-model buckets are
# preserved verbatim so callers can inspect them, but the source-selection
# shim only anchors on the two aggregate windows.
SESSION_WINDOW_KEY = "five_hour"
WEEKLY_WINDOW_KEY = "seven_day"

# Defensive key sets — the schema is undocumented, so accept several spellings.
_PCT_KEYS = (
    "utilization",
    "percent",
    "percent_used",
    "used_pct",
    "used_percent",
    "usage",
)
_RESET_KEYS = ("resets_at", "reset_at", "resetsAt", "resets")


class OAuthUsageError(Exception):
    """Base error for OAuth usage retrieval failures."""


class OAuthUnauthorized(OAuthUsageError):
    """HTTP 401 — the access token was rejected (likely just rotated)."""


class OAuthRateLimited(OAuthUsageError):
    """HTTP 429 — carries the parsed ``Retry-After`` value in seconds."""

    def __init__(self, retry_after: Optional[float]):
        super().__init__(f"rate limited (retry_after={retry_after})")
        self.retry_after = retry_after


@dataclass(frozen=True)
class UsageWindow:
    """One usage window: a utilization percentage and its reset time."""

    percent: float
    resets_at: Optional[int]  # UNIX seconds, or None when absent/unparseable


@dataclass(frozen=True)
class OAuthUsage:
    """Parsed usage response keyed by window name (best-effort)."""

    windows: Dict[str, UsageWindow]
    fetched_at: int  # UNIX seconds when the response was received

    def _window(self, key: str) -> Optional[UsageWindow]:
        return self.windows.get(key)

    @property
    def session_pct(self) -> Optional[float]:
        win = self._window(SESSION_WINDOW_KEY)
        return win.percent if win else None

    @property
    def weekly_pct(self) -> Optional[float]:
        win = self._window(WEEKLY_WINDOW_KEY)
        return win.percent if win else None

    @property
    def session_resets_at(self) -> Optional[int]:
        win = self._window(SESSION_WINDOW_KEY)
        return win.resets_at if win else None

    @property
    def weekly_resets_at(self) -> Optional[int]:
        win = self._window(WEEKLY_WINDOW_KEY)
        return win.resets_at if win else None


# --- Credential reading -----------------------------------------------------


def _credentials_path() -> Path:
    """Path to the Claude Code CLI credentials file (all platforms)."""
    return Path.home() / ".claude" / ".credentials.json"


def _extract_access_token(data: object) -> Optional[str]:
    """Pull ``claudeAiOauth.accessToken`` out of a decoded JSON blob."""
    if not isinstance(data, dict):
        return None
    oauth = data.get(_OAUTH_FIELD)
    if not isinstance(oauth, dict):
        return None
    token = oauth.get(_ACCESS_TOKEN_FIELD)
    if isinstance(token, str) and token.strip():
        return token.strip()
    return None


def _read_token_from_file() -> Optional[str]:
    """Read the access token from ``~/.claude/.credentials.json``."""
    path = _credentials_path()
    try:
        raw = path.read_text()
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.debug("credentials file %s is not valid JSON", path)
        return None
    return _extract_access_token(data)


def _read_token_from_keychain() -> Optional[str]:
    """Read the access token from the macOS keychain (best-effort).

    The Claude Code CLI stores the same ``{"claudeAiOauth": {...}}`` JSON blob
    as a generic-password item under the service name
    :data:`KEYCHAIN_SERVICE`. Only attempted on macOS; every other platform
    (and every failure) returns ``None``.
    """
    if sys.platform != "darwin":
        return None
    security = shutil.which("security")
    if not security:
        return None
    try:
        result = subprocess.run(  # noqa: S603 — fixed argv, no shell
            [security, "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            timeout=_KEYCHAIN_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("keychain lookup failed: %s", exc)
        return None
    if result.returncode != 0:
        return None
    raw = (result.stdout or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return _extract_access_token(data)


def read_access_token() -> Optional[str]:
    """Read the CLI's OAuth access token, file first then keychain.

    Returns ``None`` when no token is available (API-key user, non-Claude
    provider, or CLI not authenticated) — the caller must degrade gracefully.
    """
    return _read_token_from_file() or _read_token_from_keychain()


# --- CLI version (for the User-Agent header) --------------------------------

_cli_version_cache: Optional[str] = None


def _probe_cli_version() -> Optional[str]:
    """Run ``claude --version`` once and return a ``X.Y.Z`` token, or None."""
    binary = shutil.which("claude")
    if not binary:
        return None
    try:
        result = subprocess.run(  # noqa: S603 — resolved path, no shell
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=_VERSION_PROBE_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("claude --version probe failed: %s", exc)
        return None
    match = re.search(r"\d+\.\d+\.\d+", (result.stdout or "") + (result.stderr or ""))
    return match.group(0) if match else None


def _claude_cli_version() -> str:
    """Return a cached Claude CLI version string for the User-Agent header."""
    global _cli_version_cache
    if _cli_version_cache is None:
        _cli_version_cache = _probe_cli_version() or _DEFAULT_CLI_VERSION
    return _cli_version_cache


# --- HTTP -------------------------------------------------------------------


def _parse_retry_after(headers: object) -> Optional[float]:
    """Parse a ``Retry-After`` header value (seconds) into a float."""
    if headers is None:
        return None
    try:
        value = headers.get("Retry-After")  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        return None
    if not value:
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _http_get_usage(token: str, timeout: float) -> dict:
    """Perform one GET against the usage endpoint.

    Raises :class:`OAuthUnauthorized` on 401, :class:`OAuthRateLimited` on 429,
    and :class:`OAuthUsageError` for every other failure.
    """
    req = urllib.request.Request(USAGE_ENDPOINT, method="GET")  # noqa: S310 — https literal
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("anthropic-beta", OAUTH_BETA_HEADER)
    req.add_header("User-Agent", f"claude-code/{_claude_cli_version()}")
    req.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise OAuthUnauthorized("access token rejected (HTTP 401)") from exc
        if exc.code == 429:
            raise OAuthRateLimited(_parse_retry_after(exc.headers)) from exc
        raise OAuthUsageError(f"HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise OAuthUsageError(f"request failed: {exc.reason}") from exc

    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise OAuthUsageError("response was not valid JSON") from exc
    if not isinstance(data, dict):
        raise OAuthUsageError("response was not a JSON object")
    return data


# --- Response parsing -------------------------------------------------------


def _clamp_pct(value: float) -> float:
    return max(0.0, min(100.0, value))


def _first_number(data: dict, keys) -> Optional[float]:
    for key in keys:
        if key in data:
            try:
                return float(data[key])
            except (TypeError, ValueError):
                continue
    return None


def _first_value(data: dict, keys):
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def _parse_reset_ts(value: object) -> Optional[int]:
    """Convert a reset value (UNIX number or ISO-8601 string) to UNIX seconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit():
            return int(text)
        # ISO-8601, tolerate a trailing 'Z'.
        from datetime import datetime

        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        return int(dt.timestamp())
    return None


def _parse_window(value: object) -> Optional[UsageWindow]:
    """Parse a single window object into a :class:`UsageWindow`.

    The utilization value is treated as an already-scaled percentage (``45`` →
    45%). The endpoint is undocumented, so the raw value is debug-logged: if it
    ever turns out to be a 0–1 fraction it would round to 0% in ``usage.md``,
    and the log makes that scale mismatch diagnosable in the field.
    """
    if not isinstance(value, dict):
        return None
    pct = _first_number(value, _PCT_KEYS)
    if pct is None:
        return None
    if pct != 0 and abs(pct) < 1.0:
        logger.debug("OAuth usage window percent %r is < 1 — if the endpoint "
                     "reports a 0-1 fraction rather than 0-100, usage is "
                     "under-reported", pct)
    resets_at = _parse_reset_ts(_first_value(value, _RESET_KEYS))
    return UsageWindow(percent=_clamp_pct(pct), resets_at=resets_at)


def parse_usage_response(data: dict, fetched_at: int) -> OAuthUsage:
    """Parse the raw usage JSON into an :class:`OAuthUsage`.

    Unknown keys whose values are not window-shaped are skipped; the per-model
    buckets (``seven_day_opus``, ``seven_day_sonnet``, …) are preserved.
    """
    windows: Dict[str, UsageWindow] = {}
    for key, raw_window in data.items():
        window = _parse_window(raw_window)
        if window is not None:
            windows[str(key)] = window
    return OAuthUsage(windows=windows, fetched_at=fetched_at)


# --- Orchestration ----------------------------------------------------------


def _backoff_delay(retry_after: Optional[float], attempt: int) -> float:
    """Compute a bounded backoff delay for a 429 retry."""
    if retry_after is not None and retry_after > 0:
        return min(retry_after, _MAX_BACKOFF_SECONDS)
    # Exponential fallback: 1s, 2s, 4s … capped.
    return min(float(2 ** (attempt - 1)), _MAX_BACKOFF_SECONDS)


def fetch_usage(
    *,
    timeout: float = _DEFAULT_TIMEOUT,
    max_retries_429: int = _MAX_RETRIES_429,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], int] = lambda: int(time.time()),
) -> Optional[OAuthUsage]:
    """Fetch authoritative usage, or ``None`` on any unrecoverable condition.

    Handles the two transient cases the endpoint exhibits:

    - **401** — re-read the access token once (the CLI may have rotated it
      out from under us) and retry with the fresh token.
    - **429** — honor ``Retry-After`` (or exponential backoff) up to
      ``max_retries_429`` times.

    ``sleep`` and ``now`` are injectable for testing.
    """
    token = read_access_token()
    if not token:
        # No OAuth token: API-key user or non-Claude provider. Graceful no-op.
        return None

    reread_401 = False
    attempts_429 = 0

    while True:
        try:
            data = _http_get_usage(token, timeout)
            return parse_usage_response(data, fetched_at=now())
        except OAuthUnauthorized:
            if reread_401:
                logger.debug("OAuth usage still 401 after token re-read")
                return None
            reread_401 = True
            fresh = read_access_token()
            if not fresh or fresh == token:
                return None
            token = fresh
            continue
        except OAuthRateLimited as exc:
            attempts_429 += 1
            if attempts_429 > max_retries_429:
                logger.debug("OAuth usage 429 retries exhausted")
                return None
            sleep(_backoff_delay(exc.retry_after, attempts_429))
            continue
        except OAuthUsageError as exc:
            logger.debug("OAuth usage fetch failed: %s", exc)
            return None
