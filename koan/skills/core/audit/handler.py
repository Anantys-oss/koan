"""Koan /audit skill -- queue a codebase audit mission."""

from skills.core.audit.audit_helpers import extract_auto_fix, queue_audit_mission
from skills.core.audit.audit_runner import DEFAULT_MAX_ISSUES, extract_limit


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
    auto_fix, args = extract_auto_fix(args)

    # First word is project name, rest is extra context
    parts = args.split(None, 1)
    project_name = parts[0]
    extra_context = parts[1] if len(parts) > 1 else ""

    return queue_audit_mission(
        ctx, project_name, extra_context, max_issues, auto_fix,
        command="audit", emoji="\U0001f50e",
    )
