"""Check notifications skill — force immediate GitHub/Jira notification check."""

import os
import time

from app.signals import CHECK_NOTIFICATIONS_FILE


def handle(ctx):
    """Trigger an immediate notification check.

    Writes a signal file that the run loop picks up on its next
    sleep-cycle check (within ~10s). The signal bypasses the
    exponential backoff on both GitHub and Jira notification checks.
    """
    signal_path = os.path.join(str(ctx.koan_root), CHECK_NOTIFICATIONS_FILE)
    try:
        with open(signal_path, "w") as f:
            f.write(f"requested at {time.strftime('%H:%M:%S')}\n")
    except OSError as e:
        return f"Failed to request notification check: {e}"

    return "🔔 Notification check requested — will run within ~10s."
