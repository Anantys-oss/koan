"""Kōan report skill — weekly/monthly PR activity digest.

Posts a per-project + global Pull-Request activity report to the communication
channel as a fenced markdown code block. The time window defaults to 7 days
(``--week`` / ``/weekly_report``) or 30 days (``--month`` / ``/monthly_report``);
an explicit ``--week``/``--month`` flag always overrides the alias.
"""


def _resolve_days(command_name: str, raw_args: str) -> int:
    """Return the window in days.

    The alias sets the default (``monthly_report`` → 30, else 7); an explicit
    ``--week``/``--month`` flag in the args overrides it (last flag wins).
    """
    days = 30 if (command_name or "").lower() == "monthly_report" else 7
    for token in (raw_args or "").split():
        if token == "--week":
            days = 7
        elif token == "--month":
            days = 30
    return days


def handle(ctx):
    """Build and return the PR activity report (posted to Telegram by the framework)."""
    raw_args = ctx.args.strip() if ctx.args else ""
    days = _resolve_days(ctx.command_name, raw_args)

    from app.pr_report import build_report

    return build_report(ctx.koan_root, days)
