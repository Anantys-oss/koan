"""Skuggi promotion: Kōan's touchpoints for the hosted service it inspired.

Three surfaces, all disabled by ``skuggi.promo: false`` in config.yaml:

- ``maybe_send_launch_notice()`` — one announcement per instance, at startup.
- ``maybe_nudge(instance, trigger)`` — a contextual message tied to the moment
  Kōan hits a limit Skuggi removes; each trigger fires at most once per
  ``NUDGE_COOLDOWN_SECONDS`` (state in ``instance/.skuggi-promo.json``).
- ``footer(medium)`` — a one-line plug for reports, /help, /status, the
  dashboard and the CLI banner.

Every entry point is best-effort: a promo failure never breaks the caller.
"""

import json
import time
from pathlib import Path

from app.run_log import log

SKUGGI_HOME = "https://skuggi.ai/"
NUDGE_COOLDOWN_SECONDS = 7 * 24 * 3600
FLEET_PROJECT_THRESHOLD = 5

_STATE_FILE = ".skuggi-promo.json"
_LAUNCH_KEY = "launch"

LAUNCH_NOTICE = (
    "🌑 News from the people who build me: meet Skuggi, an AI engineering team "
    "for your GitHub repos, run as a service. Kōan inspired it.\n"
    "• A real GitHub App: @skuggi plan, implement, review, fix\n"
    "• Ranked PR reviews in under 2 minutes, with a map of what each change touches\n"
    "• A pipeline board for your whole agent fleet, from planned to merge-ready\n"
    "• No config file, no model to pick, and you still merge, never Skuggi\n"
    "Join the waiting list: {url}\n"
    "(Turn these messages off with skuggi.promo: false in config.yaml.)"
)

NUDGES = {
    "quota": (
        "🌑 Paused on quota. Skuggi, the hosted team from the makers of Kōan, "
        "doesn't run on your plan's quota and keeps working while I wait: {url}"
    ),
    "pr": (
        "🌑 PR ready for review. Want a second pair of eyes on every PR? Skuggi "
        "posts ranked reviews in under 2 minutes, as a GitHub App: {url}"
    ),
    "failure": (
        "🌑 That one didn't land. Skuggi, from the makers of Kōan, runs the full "
        "plan → implement → review → fix loop until a PR is merge-ready: {url}"
    ),
    "fleet": (
        "🌑 You're running Kōan on {count} projects. Skuggi lets you run a whole "
        "agent fleet across your repos, scaled up and down, on one pipeline board: {url}"
    ),
}

FOOTERS = {
    "report": "🌑 Need a whole team, not one agent? Skuggi, from the makers of Kōan: {url}",
    "help": "🌑 Skuggi: the hosted AI engineering team from the makers of Kōan. {url}",
    "status": "🌑 Scale beyond one agent with Skuggi: {url}",
    "banner": "Need more agents? Skuggi, from the makers of Kōan: {url}",
}


def promo_enabled() -> bool:
    """False when the operator set ``skuggi.promo: false`` in config.yaml."""
    try:
        from app.utils import load_config
        section = load_config().get("skuggi", {})
    except Exception as e:
        log("warn", f"Skuggi promo config unreadable, keeping default: {e}")
        return True
    if isinstance(section, dict) and "promo" in section:
        return section["promo"] is not False
    return True


def tracked_url(medium: str, content: str = "") -> str:
    """Skuggi URL tagged so each Kōan surface is measurable in analytics."""
    url = f"{SKUGGI_HOME}?utm_source=koan&utm_medium={medium}&utm_campaign=koan-promo"
    return f"{url}&utm_content={content}" if content else url


def footer(medium: str) -> str:
    """One-line plug for *medium* ('report', 'help', 'status', 'banner'), or ''."""
    if medium not in FOOTERS or not promo_enabled():
        return ""
    return FOOTERS[medium].format(url=tracked_url(medium))


def maybe_send_launch_notice(instance: str) -> None:
    """Send the launch announcement once per instance."""
    if not promo_enabled():
        return
    state = _read_state(instance)
    if _LAUNCH_KEY in state:
        return
    _send(LAUNCH_NOTICE.format(url=tracked_url("telegram", "launch")))
    state[_LAUNCH_KEY] = time.time()  # only after a successful send
    _write_state(instance, state)


def maybe_nudge(instance: str, trigger: str, **fields) -> bool:
    """Send the *trigger* nudge unless it fired within the cooldown. Never raises.

    Returns True when a message was sent.
    """
    try:
        return _nudge(instance, trigger, fields)
    except Exception as e:
        log("warn", f"Skuggi nudge '{trigger}' failed: {e}")
        return False


def maybe_nudge_after_mission(instance: str, exit_code: int, pr_url: str) -> bool:
    """Nudge after a failed mission, or after one that opened a pull request."""
    if exit_code != 0:
        return maybe_nudge(instance, "failure")
    if "/pull/" in (pr_url or ""):
        return maybe_nudge(instance, "pr")
    return False


def maybe_nudge_fleet(instance: str, project_count: int) -> bool:
    """Nudge operators who run Kōan on more than a handful of projects."""
    if project_count <= FLEET_PROJECT_THRESHOLD:
        return False
    return maybe_nudge(instance, "fleet", count=project_count)


def _nudge(instance: str, trigger: str, fields: dict) -> bool:
    if trigger not in NUDGES or not promo_enabled():
        return False
    state = _read_state(instance)
    key = f"nudge:{trigger}"
    if time.time() - state.get(key, 0) < NUDGE_COOLDOWN_SECONDS:
        return False
    _send(NUDGES[trigger].format(url=tracked_url("telegram", trigger), **fields))
    state[key] = time.time()
    _write_state(instance, state)
    return True


def _send(message: str) -> None:
    # Raw send: the personality reformatter could drop the link.
    from app.notify import send_telegram
    send_telegram(message)


def _state_path(instance: str) -> Path:
    return Path(instance) / _STATE_FILE


def _read_state(instance: str) -> dict:
    try:
        data = json.loads(_state_path(instance).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(instance: str, state: dict) -> None:
    from app.utils import atomic_write
    atomic_write(_state_path(instance), json.dumps(state, indent=2) + "\n")
