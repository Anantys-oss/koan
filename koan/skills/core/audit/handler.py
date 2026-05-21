"""Koan /audit skill -- queue a codebase audit mission."""

import re

from skills.core.audit.audit_runner import DEFAULT_MAX_ISSUES, extract_limit

# Matches --auto-fix or --auto-fix=<severity>
_AUTO_FIX_RE = re.compile(r"--auto-fix(?:=(\w+))?\b", re.IGNORECASE)


def _extract_auto_fix(text):
    """Extract --auto-fix[=severity] from text.

    Returns (severity_or_None, cleaned_text). When ``--auto-fix`` is
    present without ``=severity``, returns ``"high"`` (critical + high).
    """
    m = _AUTO_FIX_RE.search(text)
    if not m:
        return None, text
    severity = m.group(1) or "high"
    cleaned = (text[:m.start()] + text[m.end():]).strip()
    cleaned = re.sub(r"  +", " ", cleaned)
    return severity.lower(), cleaned


def handle(ctx):
    """Handle /audit command -- queue a codebase audit mission.

    Usage:
        /audit <project>                          -- audit (top 5 findings)
        /audit <project> <extra context>          -- audit with focus guidance
        /audit <project> <focus> limit=N          -- override max findings
    """
    args = ctx.args.strip()

    if args in ("-h", "--help"):
        return (
            "Usage: /audit <project-name> [extra context] [limit=N] [--auto-fix[=SEVERITY]]\n\n"
            "Audits a project for optimizations, simplifications, "
            "and potential issues. Creates a GitHub issue for each finding.\n\n"
            f"Default: top {DEFAULT_MAX_ISSUES} most important findings. "
            "Use limit=N to override.\n\n"
            "--auto-fix queues /fix missions for critical+high severity issues.\n"
            "--auto-fix=critical queues only critical findings.\n"
            "Max 3 auto-fix missions per audit run.\n\n"
            "Examples:\n"
            "  /audit koan\n"
            "  /audit myapp focus on the auth module\n"
            "  /audit webapp look for performance bottlenecks limit=10\n"
            "  /audit koan --auto-fix\n"
            "  /audit koan --auto-fix=critical"
        )

    if not args:
        return (
            "\u274c Usage: /audit <project-name> [extra context] [limit=N]\n"
            "Example: /audit koan focus on error handling"
        )

    # Extract flags before splitting
    max_issues, args = extract_limit(args)
    auto_fix, args = _extract_auto_fix(args)

    # First word is project name, rest is extra context
    parts = args.split(None, 1)
    project_name = parts[0]
    extra_context = parts[1] if len(parts) > 1 else ""

    return _queue_audit(ctx, project_name, extra_context, max_issues, auto_fix)


def _queue_audit(ctx, project_name, extra_context, max_issues=DEFAULT_MAX_ISSUES, auto_fix=None):
    """Queue an audit mission."""
    from app.utils import insert_pending_mission, resolve_project_path

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
    mission_entry = f"- [project:{project_name}] /audit{suffix}{limit_suffix}{fix_suffix}"
    missions_path = ctx.instance_dir / "missions.md"
    insert_pending_mission(missions_path, mission_entry)

    context_hint = f" (focus: {extra_context})" if extra_context else ""
    limit_hint = f", limit={max_issues}" if max_issues != DEFAULT_MAX_ISSUES else ""
    fix_hint = f", auto-fix={auto_fix}" if auto_fix else ""
    return f"\U0001f50e Audit queued for {project_name}{context_hint}{limit_hint}{fix_hint}"
