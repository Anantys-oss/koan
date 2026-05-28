"""Kōan ``/rtk`` skill — diagnostics and setup for the optional rtk binary.

Subcommands:
    /rtk                  — show detection status
    /rtk setup            — preview what ``rtk init -g`` would change
    /rtk setup confirm    — actually run ``rtk init -g --auto-patch``
    /rtk uninstall        — run ``rtk init -g --uninstall``
    /rtk gain [...]       — forward to ``rtk gain``
    /rtk discover [...]   — forward to ``rtk discover``

The two-step confirmation on ``setup`` is deliberate: the install command
mutates the user's global ``~/.claude/settings.json``, which is outside
Kōan's instance/ directory.  Showing the preview first surfaces what's
about to change so the user can audit before committing.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import List, Optional


_RTK_TIMEOUT = 30  # seconds — covers `rtk init -g` network/disk I/O
_GAIN_TIMEOUT = 10
_HELP = (
    "🪓 RTK — token-efficient CLI proxy.\n"
    "Usage:\n"
    "  /rtk                — status\n"
    "  /rtk setup          — preview hook install\n"
    "  /rtk setup confirm  — install hook into ~/.claude/settings.json\n"
    "  /rtk uninstall      — remove hook\n"
    "  /rtk gain [args]    — token-savings analytics\n"
    "  /rtk discover [args]— missed-savings opportunities"
)


def _format_status(status, project_setting: Optional[bool] = None) -> str:
    """Render an :class:`RtkStatus` snapshot for Telegram output."""
    lines = ["🪓 *RTK status*", ""]
    if not status.installed:
        lines.append("• Binary: not installed")
        lines.append(
            "• Install: `brew install rtk` or "
            "`curl -fsSL https://raw.githubusercontent.com/rtk-ai/rtk/refs/heads/master/install.sh | sh`"
        )
        if not status.jq_available:
            lines.append("• jq: missing (required for the auto-rewrite hook)")
        return "\n".join(lines)

    lines.append(f"• Binary: `{status.binary_path}` (version {status.version or 'unknown'})")
    if status.hook_active is True:
        lines.append("• Hook: ✅ active in `~/.claude/settings.json`")
    elif status.hook_active is False:
        lines.append("• Hook: ⚠️  not installed — run `/rtk setup` to enable")
    else:
        lines.append("• Hook: ❓ no `~/.claude/settings.json` (Claude Code never run?)")
    lines.append(f"• jq: {'✅ available' if status.jq_available else '❌ missing (hook needs it)'}")
    if status.config_path:
        lines.append(f"• Config: `{status.config_path}`")
    else:
        lines.append("• Config: (none — using rtk defaults)")

    # Surface the resolved per-project + global gate so the user knows whether
    # the awareness section will actually fire on the next mission.
    try:
        from app.config import is_rtk_awareness_enabled
        global_on = is_rtk_awareness_enabled()
    except Exception:
        global_on = False
    if project_setting is None:
        lines.append(f"• Awareness in prompts: {'on' if global_on else 'off'}")
    else:
        effective = global_on and project_setting
        lines.append(
            f"• Awareness in prompts: {'on' if effective else 'off'} "
            f"(global={global_on}, project={project_setting})"
        )
    return "\n".join(lines)


def _run_rtk(args: List[str], timeout: int = _RTK_TIMEOUT) -> tuple[int, str]:
    """Invoke rtk and return (exit_code, combined_output).

    All errors are caught so the skill always returns a renderable message
    rather than crashing the bridge.
    """
    if not shutil.which("rtk"):
        return 127, "rtk binary not found on PATH"
    try:
        result = subprocess.run(
            ["rtk", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 124, f"rtk {' '.join(args)} timed out after {timeout}s"
    except OSError as e:
        return 1, f"rtk failed to launch: {e}"
    out = (result.stdout or "") + (result.stderr or "")
    return result.returncode, out.strip() or "(no output)"


def _truncate(text: str, limit: int = 1500) -> str:
    """Trim long rtk output for Telegram while preserving the head + tail."""
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-limit // 2 :]
    return f"{head}\n…\n{tail}"


# Subcommands that simply forward to ``rtk <sub> [args]`` and pretty-print
# the result.  Mapped to (emoji, label) for the response header.
_PASSTHROUGH = {
    "gain": ("📊", "rtk gain"),
    "discover": ("🔎", "rtk discover"),
}


def _passthrough(sub: str, rest: List[str]) -> str:
    """Forward ``/rtk <sub> [args]`` to the rtk binary and render the result."""
    code, output = _run_rtk([sub, *rest], timeout=_GAIN_TIMEOUT)
    if code == 127:
        return f"❌ {output}"
    emoji, label = _PASSTHROUGH[sub]
    return f"{emoji} *{label}*\n\n```\n{_truncate(output)}\n```"


def _toggle_override(instance_dir: Path, enable: bool) -> str:
    """Write the ``instance/.koan-rtk-override`` runtime flag and report.

    The config layer treats this file as the highest-priority source for
    :func:`app.config.is_rtk_mode`, so the change takes effect on the next
    mission without editing ``config.yaml``.

    Uses :func:`app.utils.atomic_write` per the project convention for
    ``instance/`` files — the run loop may be reading the override
    concurrently and a partial-write window would briefly mask the new
    value.
    """
    from app.utils import atomic_write

    override = instance_dir / ".koan-rtk-override"
    atomic_write(override, "on\n" if enable else "off\n")
    state = "ON" if enable else "OFF"
    inverse = "/rtk off" if enable else "/rtk on"
    return (
        f"🪓 RTK awareness {state} (runtime override).\n"
        f"Takes effect on the next mission. "
        f"Reverse with `{inverse}`."
    )


def _current_project_name(koan_root: Path) -> str:
    """Best-effort current project name for project-scoped status."""
    project_file = koan_root / "instance" / ".koan-project"
    try:
        return project_file.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _resolve_project_setting(koan_root: Path) -> Optional[bool]:
    """Return the per-project rtk setting for the active project, if any."""
    project = _current_project_name(koan_root)
    if not project:
        return None
    try:
        from app.projects_config import get_project_rtk_enabled, load_projects_config
        cfg = load_projects_config(str(koan_root))
        if not cfg:
            return None
        return get_project_rtk_enabled(cfg, project)
    except Exception:
        return None


def handle(ctx) -> str:
    from app.rtk_detector import detect_rtk, reset_cache

    args = (ctx.args or "").strip()
    parts = args.split()

    # /rtk         → status
    if not parts:
        status = detect_rtk()
        project_setting = _resolve_project_setting(Path(ctx.koan_root))
        return _format_status(status, project_setting=project_setting)

    sub = parts[0].lower()
    rest = parts[1:]

    if sub in ("help", "--help", "-h"):
        return _HELP

    if sub == "setup":
        if not shutil.which("rtk"):
            return (
                "❌ rtk is not installed. Install it first:\n"
                "  `brew install rtk`\n"
                "  or `curl -fsSL https://raw.githubusercontent.com/rtk-ai/rtk/refs/heads/master/install.sh | sh`"
            )
        # Preview / confirm gate.
        if not rest or rest[0].lower() != "confirm":
            status = detect_rtk(force=True)
            if status.hook_active is True:
                return (
                    "🪓 Hook already installed in `~/.claude/settings.json`.\n"
                    "Run `/rtk uninstall` to remove it, or `/rtk setup confirm` to reinstall."
                )
            return (
                "🪓 *Setup preview*\n\n"
                "Running `rtk init -g --auto-patch` will:\n"
                "  1. Add a `PreToolUse` Bash hook to `~/.claude/settings.json`.\n"
                "  2. Drop an `RTK.md` awareness file next to it.\n"
                "  3. Restart Claude Code (any new sessions pick up the hook).\n\n"
                f"jq available: {'✅' if status.jq_available else '❌  install jq first or the hook will be a no-op'}\n\n"
                "Confirm by sending `/rtk setup confirm`."
            )
        # Confirmed — actually run the installer.
        code, output = _run_rtk(["init", "-g", "--auto-patch"])
        reset_cache()
        new_status = detect_rtk(force=True)
        if code == 0 and new_status.hook_active:
            return (
                "✅ Hook installed.\n\n"
                f"```\n{_truncate(output, 800)}\n```\n\n"
                "Restart any active Claude Code sessions to pick up the hook."
            )
        return (
            f"❌ `rtk init -g --auto-patch` exited {code}.\n\n"
            f"```\n{_truncate(output)}\n```"
        )

    if sub == "uninstall":
        if not shutil.which("rtk"):
            return "❌ rtk binary not on PATH — nothing to uninstall."
        code, output = _run_rtk(["init", "-g", "--uninstall"])
        reset_cache()
        if code == 0:
            return (
                "✅ Hook uninstalled.\n\n"
                f"```\n{_truncate(output, 800)}\n```"
            )
        return (
            f"❌ Uninstall exited {code}.\n\n"
            f"```\n{_truncate(output)}\n```"
        )

    if sub in _PASSTHROUGH:
        return _passthrough(sub, rest)

    if sub in ("on", "off"):
        return _toggle_override(Path(ctx.instance_dir), sub == "on")

    return f"Unknown subcommand: `{sub}`\n\n{_HELP}"
