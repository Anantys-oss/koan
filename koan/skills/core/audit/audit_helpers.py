"""Shared helpers for audit and security_audit skill handlers."""

import re

from skills.core.audit.audit_runner import DEFAULT_MAX_ISSUES

# Matches --auto-fix or --auto-fix=<severity>
AUTO_FIX_RE = re.compile(r"--auto-fix(?:=(\w+))?\b", re.IGNORECASE)


def extract_auto_fix(text):
    """Extract --auto-fix[=severity] from text.

    Returns (severity_or_None, cleaned_text). When ``--auto-fix`` is
    present without ``=severity``, returns ``"high"`` (critical + high).
    """
    m = AUTO_FIX_RE.search(text)
    if not m:
        return None, text
    severity = m.group(1) or "high"
    cleaned = (text[:m.start()] + text[m.end():]).strip()
    cleaned = re.sub(r"  +", " ", cleaned)
    return severity.lower(), cleaned


def queue_audit_mission(ctx, project_name, extra_context,
                        max_issues=DEFAULT_MAX_ISSUES, auto_fix=None,
                        *, command, emoji):
    """Queue an audit or security_audit mission.

    Parameters
    ----------
    command : str
        The slash command to embed in the mission entry (e.g. "audit"
        or "security_audit").
    emoji : str
        The emoji prefix for the confirmation message.
    """
    from app.utils import (
        insert_pending_mission, resolve_project_alias, resolve_project_path,
    )

    canonical = resolve_project_alias(project_name)
    if canonical:
        project_name = canonical

    path = resolve_project_path(project_name)
    if not path:
        from app.utils import get_known_projects

        known = ", ".join(n for n, _ in get_known_projects()) or "none"
        return (
            f"\u274c Unknown project '{project_name}'.\n"
            f"Known projects: {known}"
        )

    suffix = f" {extra_context}" if extra_context else ""
    limit_suffix = f" limit={max_issues}" if max_issues != DEFAULT_MAX_ISSUES else ""
    fix_suffix = ""
    if auto_fix:
        fix_suffix = f" --auto-fix={auto_fix}" if auto_fix != "high" else " --auto-fix"
    mission_entry = f"- [project:{project_name}] /{command}{suffix}{limit_suffix}{fix_suffix}"
    missions_path = ctx.instance_dir / "missions.md"
    insert_pending_mission(missions_path, mission_entry)

    # Human-friendly label: "Audit" or "Security audit"
    label = command.replace("_", " ").capitalize()
    context_hint = f" (focus: {extra_context})" if extra_context else ""
    limit_hint = f", limit={max_issues}" if max_issues != DEFAULT_MAX_ISSUES else ""
    fix_hint = f", auto-fix={auto_fix}" if auto_fix else ""
    return f"{emoji} {label} queued for {project_name}{context_hint}{limit_hint}{fix_hint}"
