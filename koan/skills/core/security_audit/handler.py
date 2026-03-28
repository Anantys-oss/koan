"""Koan /security_audit skill -- queue a security-focused audit mission."""

import re

# Matches limit=N anywhere in the args string
_LIMIT_RE = re.compile(r"\blimit=(\d+)\b", re.IGNORECASE)

DEFAULT_MAX_ISSUES = 5


def _extract_limit(text):
    """Extract limit=N from text, return (limit, cleaned_text)."""
    m = _LIMIT_RE.search(text)
    if not m:
        return DEFAULT_MAX_ISSUES, text
    limit = int(m.group(1))
    cleaned = (text[:m.start()] + text[m.end():]).strip()
    cleaned = re.sub(r"  +", " ", cleaned)
    return max(1, limit), cleaned


def handle(ctx):
    """Handle /security_audit command -- queue a security audit mission.

    Usage:
        /security_audit <project>                  -- security audit (top 5 findings)
        /security_audit <project> <extra context>  -- audit with focus guidance
        /security_audit <project> limit=N          -- override max findings
    """
    args = ctx.args.strip()

    if args in ("-h", "--help"):
        return (
            "Usage: /security_audit <project-name> [extra context] [limit=N]\n\n"
            "Performs a security-focused SDLC audit of a project. Searches for "
            "critical vulnerabilities (injection, auth flaws, secrets exposure, "
            "path traversal, SSRF, etc.) and creates a GitHub issue for each.\n\n"
            f"Default: top {DEFAULT_MAX_ISSUES} most critical findings. "
            "Use limit=N to override.\n\n"
            "Aliases: /security, /secu\n\n"
            "Examples:\n"
            "  /security_audit koan\n"
            "  /security myapp focus on the API endpoints\n"
            "  /secu webapp limit=3"
        )

    if not args:
        return (
            "\u274c Usage: /security_audit <project-name> [extra context] [limit=N]\n"
            "Example: /security_audit koan focus on input validation"
        )

    # Extract limit=N before splitting
    max_issues, args = _extract_limit(args)

    # First word is project name, rest is extra context
    parts = args.split(None, 1)
    project_name = parts[0]
    extra_context = parts[1] if len(parts) > 1 else ""

    return _queue_security_audit(ctx, project_name, extra_context, max_issues)


def _queue_security_audit(ctx, project_name, extra_context, max_issues=DEFAULT_MAX_ISSUES):
    """Queue a security audit mission."""
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
    mission_entry = f"- [project:{project_name}] /security_audit{suffix}{limit_suffix}"
    missions_path = ctx.instance_dir / "missions.md"
    insert_pending_mission(missions_path, mission_entry)

    context_hint = f" (focus: {extra_context})" if extra_context else ""
    limit_hint = f", limit={max_issues}" if max_issues != DEFAULT_MAX_ISSUES else ""
    return f"\U0001f6e1\ufe0f Security audit queued for {project_name}{context_hint}{limit_hint}"
