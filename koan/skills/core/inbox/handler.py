"""Inbox skill — force GitHub notification check and show pending mail count."""

import os
import time

from app.signals import CHECK_NOTIFICATIONS_FILE


def _count_github_missions(instance_dir):
    """Count pending missions originating from GitHub (@mention 📬 marker)."""
    missions_path = os.path.join(str(instance_dir), "missions.md")
    try:
        with open(missions_path) as f:
            content = f.read()
    except OSError:
        return 0

    from app.missions import list_pending
    pending = list_pending(content)
    return sum(1 for m in pending if "📬" in m)


def handle(ctx):
    """Force a GitHub notification check and report pending mail count."""
    signal_path = os.path.join(str(ctx.koan_root), CHECK_NOTIFICATIONS_FILE)
    try:
        with open(signal_path, "w") as f:
            f.write(f"requested at {time.strftime('%H:%M:%S')}\n")
    except OSError as e:
        return f"Failed to trigger inbox check: {e}"

    github_count = _count_github_missions(ctx.instance_dir)

    parts = ["📬 Inbox check triggered — fetching GitHub notifications."]
    if github_count > 0:
        label = "mission" if github_count == 1 else "missions"
        parts.append(f"Currently {github_count} GitHub {label} queued.")
    else:
        parts.append("No GitHub missions in queue right now.")

    return " ".join(parts)
