"""
Koan -- Private security audit runner.

Same security-focused audit pipeline as ``/security_audit``, but findings
never leave the local instance: no GitHub issues, no Private Vulnerability
Reports. Results land in today's project journal entry.

CLI:
    python3 -m skills.core.private_security_audit.private_security_audit_runner \
        --project-path <path> --project-name <name> --instance-dir <dir> \
        [--context "focus on API endpoints"] [--max-issues 5]
"""

import sys
from pathlib import Path

from skills.core.audit.audit_runner import run_audit

DEFAULT_MAX_ISSUES = 5


def run_private_security_audit(
    project_path: str,
    project_name: str,
    instance_dir: str,
    extra_context: str = "",
    max_issues: int = DEFAULT_MAX_ISSUES,
    notify_fn=None,
) -> tuple:
    """Execute a security audit and write findings to the journal only.

    Forces ``journal_only=True`` and disables PVRS so no data leaves the
    instance, regardless of project configuration.
    """
    # Reuse the security_audit prompt; this skill is a pure output-policy
    # variant of /security_audit, not a different analysis.
    sec_skill_dir = (
        Path(__file__).resolve().parent.parent / "security_audit"
    )

    return run_audit(
        project_path=project_path,
        project_name=project_name,
        instance_dir=instance_dir,
        extra_context=extra_context,
        max_issues=max_issues,
        notify_fn=notify_fn,
        skill_dir=sec_skill_dir,
        report_name="private_security_audit",
        pvrs_mode="false",
        journal_only=True,
    )


def main(argv=None):
    """CLI entry point for private_security_audit_runner."""
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Run a security audit and keep findings in the journal only "
            "(no GitHub issues, no PVRS)."
        ),
    )
    parser.add_argument("--project-path", required=True)
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--instance-dir", required=True)
    parser.add_argument("--context", default="")
    parser.add_argument("--context-file", default=None)
    parser.add_argument(
        "--max-issues", type=int, default=DEFAULT_MAX_ISSUES,
        help=f"Maximum number of findings (default: {DEFAULT_MAX_ISSUES})",
    )
    cli_args = parser.parse_args(argv)

    context = cli_args.context
    if cli_args.context_file:
        try:
            context = Path(cli_args.context_file).read_text(encoding="utf-8").strip()
        except OSError as e:
            print(f"Warning: could not read context file: {e}", file=sys.stderr)

    success, summary = run_private_security_audit(
        project_path=cli_args.project_path,
        project_name=cli_args.project_name,
        instance_dir=cli_args.instance_dir,
        extra_context=context,
        max_issues=cli_args.max_issues,
    )
    print(summary)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
