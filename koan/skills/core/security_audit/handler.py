"""Koan /security_audit skill -- queue a security-focused audit mission."""

from skills.core.audit.audit_helpers import queue_audit_mission
from skills.core.audit.audit_runner import DEFAULT_MAX_ISSUES, extract_limit
from app.github_skill_helpers import extract_auto_fix


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
            "Usage: /security_audit <project-name> [extra context] [limit=N] [--auto-fix[=SEVERITY]]\n\n"
            "Performs a security-focused SDLC audit of a project. Searches for "
            "critical vulnerabilities (injection, auth flaws, secrets exposure, "
            "path traversal, SSRF, etc.) and creates a GitHub issue for each.\n\n"
            f"Default: top {DEFAULT_MAX_ISSUES} most critical findings. "
            "Use limit=N to override.\n\n"
            "--auto-fix queues /fix missions for critical+high severity issues.\n"
            "--auto-fix=critical queues only critical findings.\n"
            "Max 3 auto-fix missions per audit run.\n\n"
            "Aliases: /security, /secu\n\n"
            "Examples:\n"
            "  /security_audit koan\n"
            "  /security myapp focus on the API endpoints\n"
            "  /secu webapp limit=3\n"
            "  /security_audit koan --auto-fix"
        )

    if not args:
        return (
            "\u274c Usage: /security_audit <project-name> [extra context] [limit=N]\n"
            "Example: /security_audit koan focus on input validation"
        )

    # Extract flags before splitting
    max_issues, args = extract_limit(args)
    auto_fix, args = extract_auto_fix(args)

    # First word is project name, rest is extra context
    parts = args.split(None, 1)
    project_name = parts[0]
    extra_context = parts[1] if len(parts) > 1 else ""

    return queue_audit_mission(
        ctx, project_name, extra_context, max_issues, auto_fix,
        command="security_audit", emoji="\U0001f6e1\ufe0f",
    )
