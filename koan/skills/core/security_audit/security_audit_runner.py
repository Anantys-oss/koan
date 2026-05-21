"""
Koan -- Security audit runner.

Thin wrapper around the audit pipeline that uses a security-focused prompt.
Reuses the full audit infrastructure (parse_findings, create_issues, etc.)
from the audit skill, only swapping the prompt and report filename.

CLI:
    python3 -m skills.core.security_audit.security_audit_runner \
        --project-path <path> --project-name <name> --instance-dir <dir> \
        [--context "focus on API endpoints"] [--max-issues 5]
"""

import sys
from pathlib import Path

from skills.core.audit.audit_runner import run_audit

DEFAULT_MAX_ISSUES = 5


def _load_pvrs_config(project_name: str) -> dict:
    """Load PVRS configuration for the project from projects.yaml.

    Returns ``{"pvrs": "auto", "pvrs_threshold": "high"}`` as defaults
    if config is unavailable.
    """
    import os
    try:
        koan_root = os.environ.get("KOAN_ROOT", "")
        if koan_root:
            from app.projects_config import (
                get_project_security_config, load_projects_config,
            )
            config = load_projects_config(koan_root)
            if config:
                return get_project_security_config(config, project_name)
    except Exception:
        pass
    return {"pvrs": "auto", "pvrs_threshold": "high"}


def run_security_audit(
    project_path: str,
    project_name: str,
    instance_dir: str,
    extra_context: str = "",
    max_issues: int = DEFAULT_MAX_ISSUES,
    notify_fn=None,
    auto_fix_severity=None,
) -> tuple:
    """Execute a security audit by delegating to run_audit with our prompt."""
    skill_dir = Path(__file__).resolve().parent

    # Load PVRS config for this project
    sec_cfg = _load_pvrs_config(project_name)

    return run_audit(
        project_path=project_path,
        project_name=project_name,
        instance_dir=instance_dir,
        extra_context=extra_context,
        max_issues=max_issues,
        notify_fn=notify_fn,
        skill_dir=skill_dir,
        report_name="security_audit",
        pvrs_mode=sec_cfg["pvrs"],
        pvrs_threshold=sec_cfg["pvrs_threshold"],
        auto_fix_severity=auto_fix_severity,
    )


def main(argv=None):
    """CLI entry point for security_audit_runner."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Security audit a project codebase and create GitHub issues."
    )
    parser.add_argument(
        "--project-path", required=True,
        help="Local path to the project repository",
    )
    parser.add_argument(
        "--project-name", required=True,
        help="Project name for labeling",
    )
    parser.add_argument(
        "--instance-dir", required=True,
        help="Path to instance directory",
    )
    parser.add_argument(
        "--context", default="",
        help="Optional focus context for the audit",
    )
    parser.add_argument(
        "--context-file", default=None,
        help="Read context from a file (for long text)",
    )
    parser.add_argument(
        "--max-issues", type=int, default=DEFAULT_MAX_ISSUES,
        help=f"Maximum number of findings (default: {DEFAULT_MAX_ISSUES})",
    )
    parser.add_argument(
        "--auto-fix", nargs="?", const="high",
        default=None, metavar="SEVERITY",
        help=(
            "Queue /fix missions for newly-created issues at or above "
            "SEVERITY (default: high). Omit SEVERITY for critical+high."
        ),
    )
    cli_args = parser.parse_args(argv)

    # Context from file takes precedence
    context = cli_args.context
    if cli_args.context_file:
        try:
            context = Path(cli_args.context_file).read_text(encoding="utf-8").strip()
        except OSError as e:
            print(f"Warning: could not read context file: {e}", file=sys.stderr)

    success, summary = run_security_audit(
        project_path=cli_args.project_path,
        project_name=cli_args.project_name,
        instance_dir=cli_args.instance_dir,
        extra_context=context,
        max_issues=cli_args.max_issues,
        auto_fix_severity=cli_args.auto_fix,
    )
    print(summary)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
