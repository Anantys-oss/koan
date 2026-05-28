"""Kōan — Detect optional `rtk` binary (https://github.com/rtk-ai/rtk).

`rtk` (Rust Token Killer) is a CLI proxy that filters and compresses common
dev-command output (git, ls, cat/read, grep/find, pytest, jest, cargo, gh,
docker, kubectl, aws, …) by 60-90 % before it reaches the LLM.  When `rtk` is
on the user's PATH, Kōan optionally:

1.  Logs detection at boot (this module).
2.  Injects an RTK awareness section into Claude's system prompt
    (:func:`app.prompt_builder._get_rtk_section`) so Claude prefers
    ``rtk <cmd>`` over the raw command.
3.  Exposes a ``/rtk`` skill (status / setup / uninstall / gain / on / off)
    that can install rtk's official ``PreToolUse`` hook into the user's
    ``~/.claude/settings.json``.

This module is **read-only** by design.  We never mutate the user's machine
state from detection — the ``/rtk setup`` skill is the only path that touches
``~/.claude/settings.json`` and it does so only after explicit Telegram
confirmation.

Resolution is cached per-process: the binary doesn't appear or disappear
mid-loop, and re-probing on every prompt build would add unnecessary
subprocess churn.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# --- Constants --------------------------------------------------------------

# Marker substrings we look for inside ~/.claude/settings.json to decide whether
# rtk's PreToolUse hook is already installed.  We accept any of:
#   - "rtk-rewrite.sh" — the hook script shipped by older rtk init -g
#   - "rtk rewrite"    — the inline command form some intermediate rtk versions used
#   - "rtk hook"       — the current form (rtk >= 0.41 ships "rtk hook claude",
#                        "rtk hook cursor", "rtk hook gemini", "rtk hook copilot")
# Any match is sufficient; this is a hint for diagnostics, not a security check.
_HOOK_MARKERS = ("rtk-rewrite.sh", "rtk rewrite", "rtk hook")

# Bound the version probe so a hung binary can't stall startup.
_VERSION_PROBE_TIMEOUT = 2.0  # seconds


# --- Data class -------------------------------------------------------------


@dataclass(frozen=True)
class RtkStatus:
    """Snapshot of rtk availability on the host.

    Attributes:
        installed: ``True`` when ``rtk`` is on PATH.
        version: ``rtk --version`` output (e.g. ``"0.28.2"``) or ``None``.
        hook_active: ``True`` when ``~/.claude/settings.json`` contains the
            rtk PreToolUse hook marker.  ``False`` when the file exists but
            no marker is present.  ``None`` when the file is missing or
            unreadable.
        jq_available: ``True`` when ``jq`` (required by rtk's hook script)
            is on PATH.  ``False`` otherwise.  Independent of ``installed``
            so the diagnostic skill can warn about it.
        config_path: Path to the user's rtk config file when present, else
            ``None``.  Looks for ``~/.config/rtk/config.toml`` (Linux/macOS
            XDG) and the macOS Application Support path.
        binary_path: Resolved path to the rtk binary, or ``None``.
    """

    installed: bool = False
    version: Optional[str] = None
    hook_active: Optional[bool] = None
    jq_available: bool = False
    config_path: Optional[Path] = None
    binary_path: Optional[Path] = None

    def summary_line(self) -> str:
        """One-line human summary for boot logs and ``/rtk`` status output."""
        if not self.installed:
            return "rtk: not installed"
        version = self.version or "unknown"
        if self.hook_active is True:
            hook = "hook: active"
        elif self.hook_active is False:
            hook = "hook: inactive"
        else:
            hook = "hook: unknown"
        jq = "" if self.jq_available else " (jq missing)"
        return f"rtk {version} detected, {hook}{jq}"


# --- Probes -----------------------------------------------------------------


def _probe_binary() -> Optional[Path]:
    """Return the resolved path to ``rtk`` on PATH, or None."""
    found = shutil.which("rtk")
    return Path(found) if found else None


def _probe_version(binary: Path) -> Optional[str]:
    """Run ``rtk --version`` once and return the version token.

    rtk emits ``rtk X.Y.Z`` on stdout.  Returns the version token (e.g.
    ``"0.28.2"``) on a match, or ``None`` for any other shape — failures
    (timeout, non-zero exit, unrecognised format) all map to ``None`` so
    callers see "unknown" instead of leaking raw subprocess output.
    """
    try:
        result = subprocess.run(
            [str(binary), "--version"],
            capture_output=True,
            text=True,
            timeout=_VERSION_PROBE_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.debug("rtk --version probe failed: %s", e)
        return None
    parts = (result.stdout or result.stderr or "").split()
    if len(parts) >= 2 and parts[0].lower() == "rtk":
        return parts[1]
    return None


def _claude_settings_path() -> Path:
    """Return the path to the user's global Claude Code settings.json.

    Claude Code reads ``~/.claude/settings.json`` regardless of platform, so
    we don't branch on macOS/Linux/Windows here.
    """
    return Path.home() / ".claude" / "settings.json"


def _probe_hook(settings_path: Optional[Path] = None) -> Optional[bool]:
    """Return whether rtk's PreToolUse hook is wired into Claude Code.

    Strategy: validate the JSON once, then substring-scan the raw text for
    rtk's hook markers.  The shape of ``hooks`` in ``settings.json`` has
    shifted between Claude Code versions, so a substring match is more
    robust than walking a specific schema.

    Returns:
        ``True`` if any marker is found, ``False`` if the file is valid
        JSON but contains no marker, ``None`` if the file is missing,
        unreadable, or not valid JSON.
    """
    path = settings_path or _claude_settings_path()
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        # OSError covers I/O failures; UnicodeDecodeError covers a
        # settings.json edited with the wrong encoding.  Either way we
        # can't confirm the hook state, but the *binary* probe must keep
        # its result — so we return None (unknown), not propagate.  Note:
        # UnicodeDecodeError is a ValueError subclass, not OSError, so it
        # has to be listed explicitly.
        logger.debug("could not read %s: %s", path, e)
        return None
    try:
        json.loads(text)
    except ValueError:
        # Broken JSON — treat as unknown rather than claiming the hook is
        # active or inactive based on a half-written config.
        return None
    return any(marker in text for marker in _HOOK_MARKERS)


def _probe_config_path() -> Optional[Path]:
    """Return the path to rtk's own config.toml, when present.

    rtk's documented locations:
      - Linux:   ``~/.config/rtk/config.toml`` (or ``$XDG_CONFIG_HOME``)
      - macOS:   ``~/Library/Application Support/rtk/config.toml``

    We never create or modify this file — only report whether it exists so
    ``/rtk`` can show the user where their settings live.
    """
    candidates = []
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        candidates.append(Path(xdg) / "rtk" / "config.toml")
    candidates.append(Path.home() / ".config" / "rtk" / "config.toml")
    if platform.system() == "Darwin":
        candidates.append(
            Path.home() / "Library" / "Application Support" / "rtk" / "config.toml"
        )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


# --- Public API -------------------------------------------------------------


_cached_status: Optional[RtkStatus] = None


def detect_rtk(force: bool = False) -> RtkStatus:
    """Return a cached :class:`RtkStatus` for this process.

    Args:
        force: When ``True``, re-runs the probes and overwrites the cache.
            Intended for tests and the ``/rtk`` skill (so users can verify
            after running ``/rtk setup``).

    The first call probes the host; subsequent calls reuse the result.  All
    probes swallow their own errors and degrade gracefully — if anything goes
    wrong we return ``RtkStatus(installed=False)`` rather than raising.
    """
    global _cached_status
    if _cached_status is not None and not force:
        return _cached_status

    try:
        binary = _probe_binary()
        if binary is None:
            status = RtkStatus(installed=False, jq_available=bool(shutil.which("jq")))
        else:
            status = RtkStatus(
                installed=True,
                version=_probe_version(binary),
                hook_active=_probe_hook(),
                jq_available=bool(shutil.which("jq")),
                config_path=_probe_config_path(),
                binary_path=binary,
            )
    except Exception as e:  # pragma: no cover — defensive; probes already swallow
        logger.warning("rtk detection failed: %s", e)
        status = RtkStatus(installed=False)

    _cached_status = status
    return status


def reset_cache() -> None:
    """Clear the cached :class:`RtkStatus`.

    Intended for tests.  Production code should rely on :func:`detect_rtk` to
    cache automatically.
    """
    global _cached_status
    _cached_status = None
