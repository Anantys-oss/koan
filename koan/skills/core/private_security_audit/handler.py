"""Koan /private_security_audit skill -- queue a journal-only security audit.

Identical UX to /security_audit, but findings are written to the daily project
journal instead of GitHub issues or Private Vulnerability Reports. Use this
when you want a security review without disclosing details to GitHub.
"""

from skills.core.audit.audit_runner import DEFAULT_MAX_ISSUES, extract_limit


def handle(ctx):
    """Handle /private_security_audit command -- queue a journal-only audit."""
    args = ctx.args.strip()

    if args in ("-h", "--help"):
        return (
            "Usage: /private_security_audit <project-name> [extra context] [limit=N]\n\n"
            "Performs a security-focused SDLC audit of a project. Findings are "
            "written ONLY to today's project journal -- no GitHub issues, no "
            "Private Vulnerability Reports.\n\n"
            f"Default: top {DEFAULT_MAX_ISSUES} most critical findings. "
            "Use limit=N to override.\n\n"
            "Aliases: /private_security, /psecu\n\n"
            "Examples:\n"
            "  /private_security_audit koan\n"
            "  /private_security myapp focus on the API endpoints\n"
            "  /psecu webapp limit=3"
        )

    if not args:
        return (
            "❌ Usage: /private_security_audit <project-name> [extra context] [limit=N]\n"
            "Example: /private_security_audit koan focus on input validation"
        )

    max_issues, args = extract_limit(args)

    parts = args.split(None, 1)
    project_name = parts[0]
    extra_context = parts[1] if len(parts) > 1 else ""

    return _queue_audit(ctx, project_name, extra_context, max_issues)


def _queue_audit(ctx, project_name, extra_context, max_issues=DEFAULT_MAX_ISSUES):
    """Queue a journal-only security audit mission."""
    from app.utils import insert_pending_mission, resolve_project_path

    path = resolve_project_path(project_name)
    if not path:
        from app.utils import get_known_projects

        known = ", ".join(n for n, _ in get_known_projects()) or "none"
        return (
            f"❌ Unknown project '{project_name}'.\n"
            f"Known projects: {known}"
        )

    suffix = f" {extra_context}" if extra_context else ""
    limit_suffix = f" limit={max_issues}" if max_issues != DEFAULT_MAX_ISSUES else ""
    mission_entry = (
        f"- [project:{project_name}] /private_security_audit{suffix}{limit_suffix}"
    )
    missions_path = ctx.instance_dir / "missions.md"
    insert_pending_mission(missions_path, mission_entry)

    context_hint = f" (focus: {extra_context})" if extra_context else ""
    limit_hint = f", limit={max_issues}" if max_issues != DEFAULT_MAX_ISSUES else ""
    return (
        f"\U0001f512 Private security audit queued for {project_name}"
        f"{context_hint}{limit_hint} -- findings will land in the journal only."
    )
