"""
Kōan -- Pre-flight quota check.

Performs a provider quota probe before each mission to verify that
API quota is available. This catches external quota consumption (manual
Claude usage, other tools) that internal token estimation cannot detect.

Probe cost is provider-specific: Claude's is free (local usage data), but
providers with no usage introspection (haze, cline) probe with a real LLM
call — so a recent SUCCESSFUL probe is cached in-process for
``preflight_cache_minutes`` (default 10) and reused across mission
attempts. Failures are never cached: a quota-exhausted verdict must
re-check every time so recovery is noticed promptly.
"""

import sys
import time
from typing import Dict, Optional, Tuple

# Monotonic timestamp of the last SUCCESSFUL probe, per provider flavor.
# In-process only: run.py is long-lived and calls the pre-flight before
# every mission attempt.
_last_probe_ok: Dict[str, float] = {}


def _reset_probe_cache() -> None:
    """Testing hook: clear the per-provider probe success cache."""
    _last_probe_ok.clear()


def _cache_minutes() -> int:
    """TTL for cached probe successes; 0 disables (fail-safe on error)."""
    try:
        from app.config import get_preflight_cache_minutes
        return get_preflight_cache_minutes()
    except Exception as e:
        print(f"[preflight] cache config read failed: {e}", file=sys.stderr)
        return 0


def preflight_quota_check(
    project_path: str,
    instance_dir: str,
    project_name: str = "",
) -> Tuple[bool, Optional[str]]:
    """Check quota availability before starting a mission.

    Args:
        project_path: Working directory for the CLI probe.
        instance_dir: Instance directory (for config access).
        project_name: Project name (for per-project provider lookup).

    Returns:
        (ok, error_message) — ok=True means quota is available,
        ok=False means quota is exhausted (error_message has details).
    """
    # Skip if budget mode is disabled
    try:
        from app.usage_tracker import _get_budget_mode
        if _get_budget_mode() == "disabled":
            return True, None
    except Exception as e:
        print(f"[preflight] Budget mode check failed: {e}", file=sys.stderr)

    # Get the provider for this project (falls back to global)
    try:
        from app.provider import get_provider
        provider = get_provider()
    except Exception as e:
        print(f"[preflight] Provider resolution failed: {e}", file=sys.stderr)
        return True, None

    ttl_minutes = _cache_minutes()
    flavor = str(getattr(provider, "name", "") or "unknown")
    if ttl_minutes > 0:
        last_ok = _last_probe_ok.get(flavor)
        if last_ok is not None:
            age = time.monotonic() - last_ok
            if age < ttl_minutes * 60:
                print(
                    f"[preflight] Using cached probe success for {flavor} "
                    f"(probed {int(age)}s ago, ttl {ttl_minutes}m)",
                    flush=True,
                )
                return True, None

    available, error_detail = provider.check_quota_available(project_path)
    if available:
        if ttl_minutes > 0:
            _last_probe_ok[flavor] = time.monotonic()
        return True, None

    return False, error_detail
