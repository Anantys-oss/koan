"""CLI provider binary availability check + in-memory degraded state.

At startup ``startup_manager.run_startup`` probes whether the configured CLI
provider's binary is findable on PATH (``CLIProvider.is_available()``). When it
is missing the agent loop must NOT start any missions — a mission would
otherwise crash the provider subprocess with ``FileNotFoundError`` — but the
loop and bridge stay alive so chat/inbox keep working.

The degraded flag is held **in memory** for the process lifetime: the PATH must
be fixed properly and the daemon restarted. There is deliberately no
auto-recovery and no on-disk signal file (see the plan / decision: "restart to
clear").

``/status`` and ``/doctor`` run in the separate bridge process and cannot read
this module's in-memory flag, so they re-probe via :func:`check_primary_cli` —
the bridge shares PATH with the loop, so the result matches (and is more
accurate than a stale flag).
"""

from __future__ import annotations

import time
from typing import NamedTuple, Optional


class CliCheck(NamedTuple):
    """Result of a primary-CLI availability probe."""

    available: bool
    binary: str
    provider_name: str


def check_primary_cli() -> CliCheck:
    """Probe whether the primary (global) CLI provider binary is on PATH.

    Pure probe — no side effects, no degraded-state mutation. Safe to call from
    any process. Delegates to the provider abstraction so absolute /
    bare-PATH / ``KOAN_ROOT``-relative paths and the ``KOAN_CLAUDE_CLI_PATH`` /
    ``cli.<role>`` overrides are all honored (``CLIProvider.is_available`` is
    ``shutil.which(self.binary())``).

    On any provider-resolution error the probe reports ``available=True`` so an
    unrelated failure never false-positives a degraded state.
    """
    try:
        from app.provider import get_provider

        provider = get_provider()
        return CliCheck(
            available=bool(provider.is_available()),
            binary=provider.binary() or "",
            provider_name=provider.name or "",
        )
    except Exception:
        return CliCheck(available=True, binary="", provider_name="")


def warning_message(binary: str, provider_name: str) -> str:
    """The operator-facing warning shown at startup and (throttled) in the loop."""
    b = binary or "(unknown)"
    p = provider_name or "(default)"
    return (
        f"⚠️ CLI binary '{b}' (provider '{p}') not found on PATH — "
        f"Kōan will keep chat & inbox running but will NOT start missions. "
        f"Fix PATH / install the CLI, then restart: make stop && make start"
    )


# ---------------------------------------------------------------------------
# In-memory degraded state (run.py loop process only)
# ---------------------------------------------------------------------------

_unavailable_info: Optional[dict] = None
_last_warned_at: float = 0.0

# Re-warn from the loop at most once per this window; startup sends the first one.
WARN_COOLDOWN_S = 6 * 3600


def set_unavailable(binary: str, provider_name: str) -> None:
    """Mark the primary CLI as unavailable for the remainder of this process."""
    global _unavailable_info
    _unavailable_info = {"binary": binary, "provider": provider_name}


def is_unavailable() -> bool:
    """True when startup detected a missing primary CLI binary."""
    return _unavailable_info is not None


def get_unavailable_info() -> Optional[dict]:
    """Return ``{"binary", "provider"}`` when degraded, else ``None``."""
    return dict(_unavailable_info) if _unavailable_info is not None else None


def clear() -> None:
    """Reset degraded state + warn throttle. Primarily a test helper."""
    global _unavailable_info, _last_warned_at
    _unavailable_info = None
    _last_warned_at = 0.0


def should_warn(cooldown_s: float = WARN_COOLDOWN_S) -> bool:
    """True when at least ``cooldown_s`` has elapsed since the last warning."""
    return (time.time() - _last_warned_at) >= cooldown_s


def mark_warned() -> None:
    """Stamp the warn throttle (called right after sending a warning)."""
    global _last_warned_at
    _last_warned_at = time.time()
