"""
Provider failover on quota exhaustion.

When the primary CLI provider hits quota limits, this module attempts to
switch to a fallback provider so the agent loop can keep working instead
of going idle.

Configuration (config.yaml):
    fallback_providers:
      - copilot
      - local

State is held in-process (module-level). A process restart resets to the
primary provider, which is the correct behavior — quota resets are
time-bounded and a restart implies enough time has passed.
"""

import sys
import time
from typing import List, Optional

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

# The provider that was active when failover was triggered.
_primary_provider: str = ""

# Timestamp when the primary was marked exhausted.
_primary_exhausted_at: float = 0.0

# Minimum seconds between recovery probes (avoid spamming the primary API).
_RECOVERY_PROBE_INTERVAL = 300  # 5 minutes

# Last time we probed the primary for recovery.
_last_recovery_probe: float = 0.0


def _get_fallback_providers() -> List[str]:
    """Read fallback_providers from config.yaml."""
    try:
        from app.utils import load_config
        config = load_config()
        raw = config.get("fallback_providers", [])
        if isinstance(raw, list):
            return [str(p).strip().lower() for p in raw if str(p).strip()]
        return []
    except Exception as e:
        print(f"[failover] Config loading failed: {e}", file=sys.stderr)
        return []


def _get_available_providers() -> dict:
    """Return the provider registry from the provider package."""
    from app.provider import _PROVIDERS
    return _PROVIDERS


def attempt_failover(current_provider: str) -> Optional[str]:
    """Try to switch to a fallback provider after quota exhaustion.

    Args:
        current_provider: Name of the provider that just hit quota.

    Returns:
        Name of the fallback provider activated, or None if no fallback
        is available.
    """
    global _primary_provider, _primary_exhausted_at, _last_recovery_probe

    fallbacks = _get_fallback_providers()
    if not fallbacks:
        return None

    providers = _get_available_providers()

    # Remember the original primary (only on the first failover in a chain).
    if not _primary_provider:
        _primary_provider = current_provider
        _primary_exhausted_at = time.time()
        _last_recovery_probe = time.time()  # Don't probe immediately after failover

    # Try each fallback in order, skipping the exhausted provider.
    for name in fallbacks:
        if name == current_provider:
            continue
        if name not in providers:
            print(
                f"[failover] Skipping unknown provider '{name}'",
                file=sys.stderr,
            )
            continue
        provider_instance = providers[name]()
        if not provider_instance.is_available():
            print(
                f"[failover] Provider '{name}' not available (binary not found)",
                file=sys.stderr,
            )
            continue

        # Activate the fallback.
        from app.provider import set_provider_override
        set_provider_override(name)
        print(
            f"[failover] Switched from '{current_provider}' to '{name}' "
            f"after quota exhaustion",
            file=sys.stderr,
        )
        return name

    # No viable fallback found.
    return None


def check_primary_recovery(project_path: str = "") -> bool:
    """Probe whether the primary provider's quota has recovered.

    Called at the top of each main loop iteration when running on a
    fallback provider.  If the primary is back, clears the override
    and returns True.

    Args:
        project_path: Path to use for quota probe (some providers
            need it for context).

    Returns:
        True if primary recovered and override was cleared.
    """
    global _last_recovery_probe

    if not _primary_provider:
        return False  # Not in failover state.

    now = time.time()
    if now - _last_recovery_probe < _RECOVERY_PROBE_INTERVAL:
        return False  # Too soon to probe again.
    _last_recovery_probe = now

    providers = _get_available_providers()
    if _primary_provider not in providers:
        return False

    provider_instance = providers[_primary_provider]()
    available, detail = provider_instance.check_quota_available(project_path)
    if available:
        from app.provider import clear_provider_override
        clear_provider_override()
        print(
            f"[failover] Primary provider '{_primary_provider}' recovered — "
            f"switching back",
            file=sys.stderr,
        )
        _reset_state()
        return True

    return False


def is_on_fallback() -> bool:
    """Return True if currently running on a fallback provider."""
    return bool(_primary_provider)


def get_failover_status() -> str:
    """Return a human-readable failover status string."""
    if not _primary_provider:
        return ""
    from app.provider import get_provider_name
    current = get_provider_name()
    elapsed = int(time.time() - _primary_exhausted_at)
    minutes = elapsed // 60
    return (
        f"Running on fallback '{current}' "
        f"(primary '{_primary_provider}' exhausted {minutes}m ago)"
    )


def _reset_state() -> None:
    """Reset all failover state (called on recovery or for testing)."""
    global _primary_provider, _primary_exhausted_at, _last_recovery_probe
    _primary_provider = ""
    _primary_exhausted_at = 0.0
    _last_recovery_probe = 0.0


def reset_for_testing() -> None:
    """Public reset for test teardown."""
    _reset_state()
    from app.provider import clear_provider_override
    clear_provider_override()
