"""Kōan recurring skill -- manage recurring missions (hourly, daily, weekly, every)."""


def handle(ctx):
    """Handle recurring mission commands.

    /daily <text>              — add a daily recurring mission
    /hourly <text>             — add an hourly recurring mission
    /weekly <text>             — add a weekly recurring mission
    /every <interval> <text>   — add a custom-interval recurring mission
    /recurring                 — list all recurring missions
    /recurring resume <X>      — re-enable a disabled recurring mission
    /recurring run [X]         — force an immediate run of a recurring mission (or all due if omitted)
    /cancel_recurring [n]      — cancel a recurring mission by number or keyword
    /pause_recurring [n]       — disable a recurring mission
    /days_recurring <n> <days> — set day-of-week filter (weekdays/weekends/mon,wed,fri)
    """
    command = ctx.command_name

    if command in ("daily", "hourly", "weekly"):
        return _handle_add(ctx, command)
    elif command == "every":
        return _handle_every(ctx)
    elif command == "recurring":
        return _handle_recurring(ctx)
    elif command == "cancel_recurring":
        return _handle_cancel(ctx)
    elif command == "pause_recurring":
        return _handle_toggle(ctx, enabled=False)
    elif command == "days_recurring":
        return _handle_days(ctx)

    return None


def _handle_add(ctx, frequency):
    """Add a recurring mission with the given frequency."""
    body = ctx.args.strip()
    if not body:
        return (
            f"Usage: /{frequency} [HH:MM] <description>\n"
            f"Ex: /{frequency} check open pull requests\n"
            f"Ex: /{frequency} 20:00 run nightly audit [project:myapp]"
        )

    from app.utils import parse_project_lenient
    from app.recurring import add_recurring, parse_at_time

    # Lenient parse: accept both [project:name] and a trailing project:name
    # hint so forgetting the brackets doesn't silently drop the project.
    project, text = parse_project_lenient(body)

    try:
        at_time, text = parse_at_time(text)
    except ValueError as e:
        return str(e)

    recurring_path = ctx.instance_dir / "recurring.json"

    try:
        add_recurring(recurring_path, frequency, text, project, at=at_time)
        ack = f"Recurring mission added ({frequency}"
        if at_time:
            ack += f" at {at_time}"
        ack += ")"
        if project:
            ack += f" [project:{project}]"
        ack += f":\n\n{text}"
        return ack
    except ValueError as e:
        return str(e)


def _handle_every(ctx):
    """Add a recurring mission with a custom interval."""
    body = ctx.args.strip()
    if not body:
        return (
            "Usage: /every <interval> <description>\n"
            "Ex: /every 5m check design issues [project:nocrm]\n"
            "Ex: /every 2h run health check\n"
            "Intervals: 5m, 30m, 2h, 1h30m"
        )

    # First word is the interval
    parts = body.split(None, 1)
    if len(parts) < 2:
        return (
            "Usage: /every <interval> <description>\n"
            "Ex: /every 5m check design issues"
        )

    interval_str, rest = parts[0], parts[1]

    from app.utils import parse_project_lenient
    from app.recurring import parse_interval, format_interval, add_recurring_interval

    try:
        interval_seconds = parse_interval(interval_str)
    except ValueError as e:
        return str(e)

    project, text = parse_project_lenient(rest)
    if not text.strip():
        return "Missing mission description after interval."

    recurring_path = ctx.instance_dir / "recurring.json"
    display = format_interval(interval_seconds)

    add_recurring_interval(recurring_path, interval_seconds, display, text, project)
    ack = f"Recurring mission added (every {display})"
    if project:
        ack += f" [project:{project}]"
    ack += f":\n\n{text}"
    return ack


def _handle_recurring(ctx):
    """Route /recurring sub-commands: list, resume <X>, run [X]."""
    args = ctx.args.strip()

    if not args:
        # No args — list all missions
        return _handle_list(ctx)

    # Parse first token for sub-command
    parts = args.split(None, 1)
    sub_command = parts[0].lower()
    remaining_args = parts[1].strip() if len(parts) > 1 else ""

    if sub_command == "resume":
        if not remaining_args:
            from app.recurring import list_recurring, format_recurring_list
            recurring_path = ctx.instance_dir / "recurring.json"
            missions = list_recurring(recurring_path)
            if missions:
                msg = format_recurring_list(missions)
                msg += "\n\nUsage: /recurring resume <number or keyword>"
                return msg
            return "No recurring missions configured."
        # Create a temporary context with the remaining args for _handle_toggle
        ctx_copy = ctx
        ctx_copy.args = remaining_args
        return _handle_toggle(ctx_copy, enabled=True)
    elif sub_command == "run":
        # Force run with optional identifier
        return _handle_run(ctx, identifier=remaining_args if remaining_args else None)
    else:
        # Treat as list (or could be a legacy sub-command)
        return _handle_list(ctx)


def _handle_list(ctx):
    """List all recurring missions."""
    from app.recurring import list_recurring, format_recurring_list

    recurring_path = ctx.instance_dir / "recurring.json"
    missions = list_recurring(recurring_path)
    return format_recurring_list(missions)


def _handle_cancel(ctx):
    """Cancel a recurring mission by number or keyword."""
    from app.recurring import list_recurring, format_recurring_list, remove_recurring

    recurring_path = ctx.instance_dir / "recurring.json"
    identifier = ctx.args.strip()

    if not identifier:
        missions = list_recurring(recurring_path)
        if missions:
            msg = format_recurring_list(missions)
            msg += "\n\nUsage: /cancel_recurring <number or keyword>"
            return msg
        return "No recurring missions to cancel."

    try:
        removed = remove_recurring(recurring_path, identifier)
        return f"Recurring mission removed: {removed}"
    except ValueError as e:
        return str(e)


def _handle_toggle(ctx, enabled):
    """Enable or disable a recurring mission."""
    from app.recurring import list_recurring, format_recurring_list, toggle_recurring

    recurring_path = ctx.instance_dir / "recurring.json"
    identifier = ctx.args.strip()
    action = "resume" if enabled else "pause"

    if not identifier:
        missions = list_recurring(recurring_path)
        if missions:
            msg = format_recurring_list(missions)
            msg += f"\n\nUsage: /{action}_recurring <number or keyword>"
            return msg
        return "No recurring missions configured."

    try:
        toggled = toggle_recurring(recurring_path, identifier, enabled)
        status = "enabled ✅" if enabled else "disabled ⏸️"
        return f"Recurring mission {status}: {toggled}"
    except ValueError as e:
        return str(e)


def _handle_run(ctx, identifier=None):
    """Force an immediate run of recurring mission(s)."""
    from app.recurring import list_recurring, format_recurring_list, force_run

    recurring_path = ctx.instance_dir / "recurring.json"
    missions_path = ctx.instance_dir / "missions.md"

    if not identifier:
        # No identifier — show list and ask for confirmation
        missions = list_recurring(recurring_path)
        if missions:
            msg = format_recurring_list(missions)
            msg += "\n\nUsage: /recurring run <number or keyword>\nOmit number to run all enabled due missions."
            return msg
        return "No recurring missions configured."

    # Attempt to force run
    try:
        injected = force_run(recurring_path, missions_path, identifier=identifier)
        if injected:
            return f"Forced run of {len(injected)} mission(s):\n" + "\n".join(f"  • {text}" for text in injected)
        return "No missions matched the identifier."
    except ValueError as e:
        return str(e)


def _handle_days(ctx):
    """Set or clear the days-of-week filter on a recurring mission."""
    from app.recurring import list_recurring, format_recurring_list, set_days

    recurring_path = ctx.instance_dir / "recurring.json"
    args = ctx.args.strip()

    if not args:
        missions = list_recurring(recurring_path)
        if missions:
            msg = format_recurring_list(missions)
            msg += (
                "\n\nUsage: /days_recurring <number> <days>\n"
                "Days: weekdays, weekends, or mon,tue,wed,thu,fri,sat,sun\n"
                "Clear: /days_recurring <number> all"
            )
            return msg
        return "No recurring missions configured."

    parts = args.split(None, 1)
    identifier = parts[0]
    days_spec = parts[1].strip() if len(parts) > 1 else None

    if not days_spec:
        return (
            "Usage: /days_recurring <number> <days>\n"
            "Days: weekdays, weekends, or mon,tue,wed,thu,fri,sat,sun\n"
            "Clear: /days_recurring <number> all"
        )

    # "all" clears the filter
    if days_spec.lower() == "all":
        days_spec = None

    try:
        updated = set_days(recurring_path, identifier, days_spec)
        if days_spec:
            return f"Days filter set to '{days_spec}': {updated}"
        return f"Days filter cleared (runs every day): {updated}"
    except ValueError as e:
        return str(e)
