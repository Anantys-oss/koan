"""Automatic Codex CLI self-update checker.

When the active CLI provider is Codex, Kōan periodically checks whether a
newer Codex CLI release is available (``codex update --check``) and applies it
(``codex update``). On a successful update the owner is notified via the
messaging bridge with the version delta.

Cadence: at startup and at most once per day. The daily throttle (a timestamp
in ``instance/.codex-update-check.json``) keeps frequent restarts from
hammering the network while still guaranteeing a check on a long-running
session.

Config (config.yaml):
    codex_update:
        enabled: true       # default: true (opt-out)
        notify: true        # default: true

State file:
    instance/.codex-update-check.json  — last check timestamp + last seen version
"""

import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from app.run_log import log

TRACKER_FILE = ".codex-update-check.json"
MIN_CHECK_INTERVAL_HOURS = 24
_CHECK_TIMEOUT = 60
_UPDATE_TIMEOUT = 300

# `codex update --check` output heuristics. Older Codex builds have no
# --check flag (they error "unexpected argument"); in that case we fall back
# to running `codex update` — which is idempotent — and confirm via a version
# delta, so the feature degrades gracefully across Codex versions.
_UNSUPPORTED_RE = re.compile(
    r"unexpected argument|unrecognized|unknown (?:option|flag|argument)",
    re.IGNORECASE,
)
_UP_TO_DATE_RE = re.compile(
    r"up[\s-]?to[\s-]?date|already .*latest|latest version|no update",
    re.IGNORECASE,
)
_UPDATE_AVAIL_RE = re.compile(
    r"update available|new(?:er)? version|can be updated|->|→",
    re.IGNORECASE,
)
_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+(?:[-.\w]*)?)")


def _load_config() -> dict:
    """Load the codex_update config section with defaults (opt-out)."""
    try:
        from app.utils import load_config
        cfg = load_config()
    except Exception as e:
        log("update", f"[codex-update] Config load failed, using defaults: {e}")
        cfg = {}
    section = cfg.get("codex_update", {})
    if not isinstance(section, dict):
        section = {}
    return {
        "enabled": bool(section.get("enabled", True)),
        "notify": bool(section.get("notify", True)),
    }


def _codex_is_active() -> bool:
    """True when the active CLI provider resolves to Codex."""
    try:
        from app.provider import get_provider_name
        return get_provider_name() == "codex"
    except Exception as e:
        log("update", f"[codex-update] provider resolution failed: {e}")
        return False


def _codex_binary() -> Optional[str]:
    """Resolve the Codex binary the provider uses, or None if unavailable."""
    binary = "codex"
    try:
        from app.provider import get_provider
        binary = get_provider().binary() or "codex"
    except Exception as e:
        log("update", f"[codex-update] binary resolution failed, using 'codex': {e}")
        binary = "codex"
    if shutil.which(binary) or Path(binary).exists():
        return binary
    return None


def _load_tracker(instance_dir: str) -> dict:
    path = Path(instance_dir) / TRACKER_FILE
    if not path.exists():
        return {}
    try:
        import json
        return json.loads(path.read_text())
    except ValueError:
        return {}  # corrupt JSON — safe to ignore, treated as "no prior check"
    except OSError as e:
        # A persistent read failure (e.g. permission denied) silently defeats
        # the daily throttle, so surface it rather than hammering the network.
        log("error", f"[codex-update] tracker read failed: {e}")
        return {}


def _record_check(instance_dir: str, version: str) -> None:
    """Persist the check timestamp (and last seen version) for throttling."""
    from app.utils import atomic_write_json
    path = Path(instance_dir) / TRACKER_FILE
    atomic_write_json(
        path,
        {"last_check_ts": time.time(), "version": version},
        indent=2,
    )


def _due_for_check(instance_dir: str) -> bool:
    """True when at least MIN_CHECK_INTERVAL_HOURS have passed since last check."""
    last = _load_tracker(instance_dir).get("last_check_ts")
    if not isinstance(last, (int, float)):
        return True
    return (time.time() - last) >= MIN_CHECK_INTERVAL_HOURS * 3600


def _run_codex(binary: str, args: list, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        [binary, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _codex_version(binary: str) -> str:
    """Return the Codex CLI semantic version, or "" if it can't be read."""
    try:
        result = _run_codex(binary, ["--version"], 15)
    except Exception as e:
        log("update", f"[codex-update] `codex --version` failed: {e}")
        return ""
    match = _VERSION_RE.search((result.stdout or "") + " " + (result.stderr or ""))
    return match.group(1) if match else ""


def _check_update_available(binary: str) -> Optional[bool]:
    """Run ``codex update --check``.

    Returns True/False when the output is conclusive, None when it can't be
    determined (e.g. the flag is unsupported on this Codex build).
    """
    try:
        result = _run_codex(binary, ["update", "--check"], _CHECK_TIMEOUT)
    except Exception as e:
        log("update", f"[codex-update] `update --check` failed: {e}")
        return None
    text = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    if _UNSUPPORTED_RE.search(text):
        return None
    if _UP_TO_DATE_RE.search(text):
        return False
    if _UPDATE_AVAIL_RE.search(text):
        return True
    # Inconclusive output. A non-zero exit here is a genuine runtime error, not
    # the benign "unsupported flag" case — log it before escalating to a full
    # `codex update` on the caller's inconclusive path.
    if result.returncode != 0:
        log("error", f"[codex-update] `update --check` exited {result.returncode}: {text}")
    return None


def _notify_update(instance_dir: str, old: str, new: str) -> None:
    """Advertise the applied update via the messaging bridge."""
    msg = (
        f"⬆️ Codex CLI updated: **{old or '?'} → {new}**\n"
        f"Ran `codex update` automatically (new upstream release detected)."
    )
    try:
        from app.notify import send_telegram
        send_telegram(msg)
    except Exception as e:
        log("error", f"[codex-update] notify failed: {e}")


def check_codex_update(
    koan_root: str,
    instance_dir: str,
    force: bool = False,
) -> bool:
    """Check for and apply a Codex CLI update.

    Only runs when the active provider is Codex and the feature is enabled.
    Throttled to once per day unless ``force`` is set. Returns True when an
    update was actually applied.
    """
    cfg = _load_config()
    if not cfg["enabled"] or not _codex_is_active():
        return False
    if not force and not _due_for_check(instance_dir):
        return False

    binary = _codex_binary()
    if binary is None:
        log("update", "[codex-update] Codex binary not found, skipping")
        return False

    current = _codex_version(binary)
    _record_check(instance_dir, current)

    available = _check_update_available(binary)
    if available is False:
        log("update", f"[codex-update] Codex {current or '?'} is up to date")
        return False

    # available is True or inconclusive → run the idempotent `codex update`
    # and confirm via a version delta so we only notify on a real change.
    log("update", "[codex-update] Running `codex update`...")
    try:
        result = _run_codex(binary, ["update"], _UPDATE_TIMEOUT)
    except Exception as e:
        log("error", f"[codex-update] `codex update` failed: {e}")
        return False
    if result.returncode != 0:
        # Non-zero exit (network error, permission denied writing the binary).
        # Log stderr distinctly instead of misreporting it as "No version change".
        stderr = (result.stderr or "").strip()
        log("error", f"[codex-update] `codex update` exited {result.returncode}: {stderr}")
        return False

    new = _codex_version(binary)
    if not new or new == current:
        log("update", f"[codex-update] No version change ({current or '?'})")
        return False

    _record_check(instance_dir, new)
    log("update", f"[codex-update] Updated Codex {current or '?'} → {new}")
    if cfg["notify"]:
        _notify_update(instance_dir, current, new)
    return True
