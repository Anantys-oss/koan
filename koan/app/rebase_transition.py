"""Transitional notice for the ``/rebase`` default-behavior change.

Historically ``/rebase`` rebased a PR **and** applied review feedback. The
feedback leg now lives behind an explicit ``--fix`` (see
``rebase_pr._FEEDBACK_ON_BY_DEFAULT`` and
``skill_dispatch._build_rebase_cmd``). For a short window we surface a notice on
the bare-rebase path so the changed meaning is not a silent surprise. After the
deadline the notice disappears automatically.

Only the *notice* is date-gated; the behavior change (a bare ``/rebase`` only
rebases) is permanent from day one. This module is kept separate from the heavy
``rebase_pr`` module so both the bridge-side handler and the runner subprocess
can import it cheaply.

Follows the ``update_hint.py`` precedent: a module-level constant plus
``datetime.now(timezone.utc)``.
"""

from datetime import datetime, timezone
from typing import Optional

# The notice stops showing after this date (~1 month after the change shipped,
# 2026-07-17). Bump/remove together with the notice code once it has elapsed.
FIX_NOTICE_DEADLINE = datetime(2026, 8, 17, tzinfo=timezone.utc)

_NOTICE_BODY = (
    "`/rebase` now only rebases the PR onto its base branch. To also address "
    "review feedback (the previous default), use `/rebase --fix` — this is "
    "implied when you add a focus area or severity after the URL."
)


def notice_active(now: Optional[datetime] = None) -> bool:
    """Whether the transition notice should still be shown.

    Args:
        now: Optional clock override for deterministic tests.
    """
    return (now or datetime.now(timezone.utc)) < FIX_NOTICE_DEADLINE


def chat_notice() -> str:
    """Plain-text transition notice for chat (Telegram/Slack) replies."""
    return f"ℹ️ Heads up: {_NOTICE_BODY}"


def pr_comment_notice() -> str:
    """GitHub-flavored transition notice as a NOTE alert callout."""
    from app.github_alerts import build_alert

    return build_alert("NOTE", _NOTICE_BODY)
