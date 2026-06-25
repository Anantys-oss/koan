"""Kōan report skill — weekly/monthly PR activity digest.

Posts a per-project + global Pull-Request activity report to the communication
channel as a fenced markdown code block. With an explicit ``--week`` (7 days) or
``--month`` (30 days) flag — or the ``/weekly_report`` / ``/monthly_report``
aliases — only that window is reported. A plain ``/report`` with no flag
produces both the weekly and the monthly report.
"""


def _resolve_windows(command_name: str, raw_args: str) -> list:
    """Return the windows (in days) to report, in display order.

    The aliases pin a single window (``weekly_report`` → ``[7]``,
    ``monthly_report`` → ``[30]``). Explicit ``--week`` / ``--month`` flags
    select those windows. A plain ``/report`` with no flag returns ``[7, 30]``
    so the default digest covers both the week and the month.
    """
    name = (command_name or "").lower()
    if name == "weekly_report":
        return [7]
    if name == "monthly_report":
        return [30]

    days = []
    for token in (raw_args or "").split():
        if token == "--week" and 7 not in days:
            days.append(7)
        elif token == "--month" and 30 not in days:
            days.append(30)

    return days or [7, 30]


def handle(ctx):
    """Build and return the PR activity report (posted to Telegram by the framework)."""
    raw_args = ctx.args.strip() if ctx.args else ""
    windows = _resolve_windows(ctx.command_name, raw_args)

    from app.pr_report import build_report

    return "\n\n".join(build_report(ctx.koan_root, days) for days in windows)
