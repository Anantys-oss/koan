"""Inbox skill — force GitHub notification check and show pending mail count."""

import os
import sys
import time

from app.signals import CHECK_NOTIFICATIONS_FILE


def _count_github_missions(instance_dir):
    """Count pending missions originating from GitHub (@mention 📬 marker)."""
    try:
        from app.mission_store import MissionStore
        store = MissionStore.load()
    except Exception as e:
        print(f"[inbox] error loading mission store: {e}", file=sys.stderr)
        return 0

    return sum(
        1 for r in store.get_by_status("pending") if r.origin_marker() == "📬"
    )


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
