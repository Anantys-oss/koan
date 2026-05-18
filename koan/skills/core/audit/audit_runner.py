"""
Koan -- Codebase audit runner.

Performs a read-only audit of a project codebase, parses the structured
findings, and creates individual GitHub issues for each one.

Pipeline:
1. Build audit prompt with project context and optional extra guidance
2. Run Claude Code CLI (read-only tools) to analyze the codebase
3. Parse Claude's structured findings (---FINDING--- blocks)
4. Enforce max_issues limit (keep only top N by severity)
5. Create a GitHub issue for each finding
6. Save audit summary to project learnings

CLI:
    python3 -m skills.core.audit.audit_runner \
        --project-path <path> --project-name <name> --instance-dir <dir> \
        [--context "focus on auth module"] [--max-issues 5]
"""

import re
import sys
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

from app.prompts import load_prompt_or_skill

DEFAULT_MAX_ISSUES = 5

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class AuditFinding:
    """A single finding from the audit."""

    __slots__ = (
        "title", "severity", "category", "location",
        "problem", "why", "suggested_fix", "effort",
    )

    def __init__(
        self,
        title: str = "",
        severity: str = "medium",
        category: str = "",
        location: str = "",
        problem: str = "",
        why: str = "",
        suggested_fix: str = "",
        effort: str = "medium",
    ):
        self.title = title
        self.severity = severity
        self.category = category
        self.location = location
        self.problem = problem
        self.why = why
        self.suggested_fix = suggested_fix
        self.effort = effort

    def is_valid(self) -> bool:
        """Check if the finding has the minimum required fields."""
        return bool(self.title and self.problem and self.location)


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

def build_audit_prompt(
    project_name: str,
    extra_context: str = "",
    skill_dir: Optional[Path] = None,
    max_issues: int = DEFAULT_MAX_ISSUES,
) -> str:
    """Build the audit prompt with optional extra context and issue limit."""
    context_block = ""
    if extra_context:
        context_block = (
            f"## Additional Focus\n\n"
            f"The human has asked you to pay special attention to:\n"
            f"> {extra_context}\n\n"
            f"Prioritize findings related to this guidance, but don't "
            f"ignore other significant issues you discover."
        )

    return load_prompt_or_skill(
        skill_dir, "audit",
        PROJECT_NAME=project_name,
        EXTRA_CONTEXT=context_block,
        MAX_ISSUES=str(max_issues),
    )


# ---------------------------------------------------------------------------
# Claude CLI integration
# ---------------------------------------------------------------------------

def _run_claude_audit(prompt: str, project_path: str) -> str:
    """Run Claude CLI with read-only tools and return the output text."""
    from app.cli_provider import run_command_streaming
    from app.config import get_analysis_max_turns, get_skill_timeout

    return run_command_streaming(
        prompt, project_path,
        allowed_tools=["Read", "Glob", "Grep", "Bash(git log:*)"],
        max_turns=get_analysis_max_turns(),
        timeout=get_skill_timeout(),
    )


# ---------------------------------------------------------------------------
# Finding parser
# ---------------------------------------------------------------------------

_FIELD_RE = re.compile(
    r"^(TITLE|SEVERITY|CATEGORY|LOCATION|PROBLEM|WHY|SUGGESTED_FIX|EFFORT):\s*(.+)",
    re.MULTILINE,
)


def parse_findings(raw_output: str) -> List[AuditFinding]:
    """Parse ---FINDING--- blocks from Claude's output."""
    blocks = re.split(r"---FINDING---", raw_output)

    findings = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue

        finding = AuditFinding()
        for match in _FIELD_RE.finditer(block):
            field = match.group(1).lower()
            value = match.group(2).strip()

            # For multiline fields, capture everything until the next field
            end_pos = match.end()
            next_field = _FIELD_RE.search(block[end_pos:])
            if next_field:
                full_value = block[match.start(2):end_pos + next_field.start()].strip()
            else:
                full_value = block[match.start(2):].strip()

            # Use the full multiline value for content fields
            if field in ("problem", "why", "suggested_fix"):
                value = full_value

            setattr(finding, field, value)

        if finding.is_valid():
            findings.append(finding)

    return findings


def prioritize_findings(
    findings: List[AuditFinding],
    max_issues: int = DEFAULT_MAX_ISSUES,
) -> List[AuditFinding]:
    """Keep only the top *max_issues* findings, ranked by severity.

    Severity order: critical > high > medium > low.
    Ties preserve the original order from the audit output.
    """
    if len(findings) <= max_issues:
        return findings

    # Stable sort by severity (critical first)
    ranked = sorted(
        findings,
        key=lambda f: _SEVERITY_ORDER.get(f.severity, 99),
    )
    return ranked[:max_issues]


# ---------------------------------------------------------------------------
# GitHub issue creation
# ---------------------------------------------------------------------------

class IssueCreationResult(NamedTuple):
    """Outcome of ``create_issues()``.

    ``urls`` lists every issue/advisory URL associated with the audit
    findings — both newly opened and those already tracked. ``created``
    and ``reused`` distinguish the two so the summary can report
    accurately.
    """

    urls: List[str]
    created: int
    reused: int


def _normalize_title(title: str) -> str:
    """Lowercase + strip + collapse whitespace for title dedup matching."""
    return " ".join((title or "").lower().split())


def _build_existing_title_index(
    existing_issues: List[Dict],
) -> Dict[str, str]:
    """Map normalized title -> issue URL for fast dedup lookup."""
    index = {}
    for issue in existing_issues:
        title = issue.get("title") or ""
        url = issue.get("url") or ""
        if not title or not url:
            continue
        key = _normalize_title(title)
        # First occurrence wins (newest issue listed first by gh)
        index.setdefault(key, url)
    return index


def _find_existing_match(
    finding: AuditFinding,
    existing_index: Dict[str, str],
) -> Optional[str]:
    """Return the URL of an open audit issue already tracking *finding*.

    Matches on the title that ``_submit_public_issue()`` would produce,
    plus the ``"Security: "``-prefixed PVRS-fallback variant so reruns
    of /security_audit also dedup against earlier public-issue runs.
    """
    if not existing_index:
        return None
    candidates = [
        finding.title,
        f"Security: {finding.title}",
    ]
    for candidate in candidates:
        url = existing_index.get(_normalize_title(candidate))
        if url:
            return url
    return None


_SEVERITY_LABELS = {
    "critical": "\U0001f534",  # red circle
    "high": "\U0001f7e0",      # orange circle
    "medium": "\U0001f7e1",    # yellow circle
    "low": "\U0001f7e2",       # green circle
}

_EFFORT_LABELS = {
    "small": "\u26a1 Quick fix",
    "medium": "\U0001f6e0\ufe0f Moderate effort",
    "large": "\U0001f3d7\ufe0f Significant work",
}


def _build_issue_body(finding: AuditFinding) -> str:
    """Build a GitHub issue body from a finding."""
    severity_icon = _SEVERITY_LABELS.get(finding.severity, "\u2753")
    effort_label = _EFFORT_LABELS.get(finding.effort, finding.effort)

    lines = [
        f"## Problem",
        f"",
        f"{finding.problem}",
        f"",
        f"## Why This Matters",
        f"",
        f"{finding.why}",
        f"",
        f"## Suggested Fix",
        f"",
        f"{finding.suggested_fix}",
        f"",
        f"## Details",
        f"",
        f"| | |",
        f"|---|---|",
        f"| **Severity** | {severity_icon} {finding.severity.capitalize()} |",
        f"| **Category** | {finding.category} |",
        f"| **Location** | `{finding.location}` |",
        f"| **Effort** | {effort_label} |",
        f"",
        f"---",
        f"\U0001f916 Created by K\u014dan from audit session",
    ]
    return "\n".join(lines)


def _build_advisory_description(finding: AuditFinding) -> str:
    """Build a PVRS advisory description from a finding.

    Similar to ``_build_issue_body()`` but formatted for the PVRS description
    field (pure markdown, no table metadata — structured fields go in the
    JSON payload).
    """
    lines = [
        f"## Problem",
        f"",
        f"{finding.problem}",
        f"",
        f"## Why This Matters",
        f"",
        f"{finding.why}",
        f"",
        f"## Suggested Fix",
        f"",
        f"{finding.suggested_fix}",
        f"",
        f"**Location**: `{finding.location}`",
        f"**Category**: {finding.category}",
        f"",
        f"---",
        f"\U0001f916 Reported by K\u014dan security audit",
    ]
    return "\n".join(lines)


def _should_use_pvrs(severity: str, threshold: str) -> bool:
    """Return True if a finding's severity meets the PVRS routing threshold.

    Findings at or above the threshold severity are routed to PVRS.
    E.g., threshold ``"high"`` routes ``critical`` and ``high`` to PVRS.
    """
    finding_rank = _SEVERITY_ORDER.get(severity, 99)
    threshold_rank = _SEVERITY_ORDER.get(threshold, 1)
    return finding_rank <= threshold_rank


def create_issues(
    findings: List[AuditFinding],
    project_path: str,
    notify_fn=None,
    pvrs_mode: str = "auto",
    pvrs_threshold: str = "high",
) -> IssueCreationResult:
    """Create GitHub issues (or PVRS reports) for each finding.

    Before opening a new issue, the repo's currently-open audit issues
    are fetched and findings whose titles already match an open issue
    are skipped — the existing issue URL is reused so reruns of the
    audit do not duplicate previously-tracked findings.

    When PVRS is available and ``pvrs_mode`` is not ``"false"``, findings
    at or above ``pvrs_threshold`` severity are submitted as private
    vulnerability reports.  Lower-severity findings and PVRS failures
    fall back to public GitHub issues. Dedup applies only to the
    public-issue path; PVRS advisories are private and cannot be
    listed with the same call.

    Args:
        findings: List of validated audit findings.
        project_path: Local path to the project repository.
        notify_fn: Optional callback for progress notifications.
        pvrs_mode: ``"auto"`` (detect at runtime), ``"true"`` (force),
            or ``"false"`` (always use public issues).
        pvrs_threshold: Minimum severity for PVRS routing (default ``"high"``).

    Returns:
        ``IssueCreationResult`` with all URLs plus created/reused counts.
    """
    from app.github import (
        check_pvrs_enabled, detect_ecosystem,
        list_open_audit_issues, resolve_target_repo,
    )

    target_repo = resolve_target_repo(project_path)

    # Determine PVRS availability
    pvrs_available = False
    if pvrs_mode == "true":
        pvrs_available = True
    elif pvrs_mode != "false" and target_repo:
        pvrs_available = check_pvrs_enabled(target_repo, cwd=project_path)

    if pvrs_available and notify_fn:
        notify_fn(
            f"  \U0001f512 PVRS enabled — "
            f"routing {pvrs_threshold}+ findings privately"
        )

    # Fetch existing audit issues once so we can dedup against them.
    # Errors are swallowed inside list_open_audit_issues — a failed
    # lookup yields an empty index, which means we fall back to the
    # legacy "create unconditionally" behavior rather than skipping
    # legitimate work.
    existing_index = _build_existing_title_index(
        list_open_audit_issues(repo=target_repo, cwd=project_path)
    )

    ecosystem = detect_ecosystem(project_path) if pvrs_available else "other"
    # Derive a package name from the project directory
    package_name = Path(project_path).name

    issue_urls = []
    created_count = 0
    reused_count = 0

    for i, finding in enumerate(findings, 1):
        title = finding.title
        use_pvrs = pvrs_available and _should_use_pvrs(
            finding.severity, pvrs_threshold,
        )

        # Dedup applies to the public-issue path only — PVRS advisories
        # live in a private endpoint not covered by `gh issue list`.
        if not use_pvrs:
            existing_url = _find_existing_match(finding, existing_index)
            if existing_url:
                reused_count += 1
                issue_urls.append(existing_url)
                if notify_fn:
                    notify_fn(
                        f"  ↩️ {i}/{len(findings)}: "
                        f"already tracked — {existing_url}"
                    )
                continue

        if notify_fn:
            channel = "\U0001f512 PVRS" if use_pvrs else "\U0001f4dd issue"
            notify_fn(
                f"  {channel} {i}/{len(findings)}: {title}"
            )

        try:
            if use_pvrs:
                url = _submit_pvrs_report(
                    finding, ecosystem, package_name,
                    target_repo, project_path,
                )
            else:
                url = _submit_public_issue(
                    finding, target_repo, project_path,
                )
        except Exception as e:
            # PVRS fallback: try public issue if PVRS submission failed
            if use_pvrs:
                print(
                    f"[audit] PVRS failed for '{title}', "
                    f"falling back to redacted public issue: {e}",
                    file=sys.stderr,
                )
                if notify_fn:
                    notify_fn(
                        f"  \u26a0\ufe0f PVRS failed for '{title}', "
                        f"creating redacted placeholder issue"
                    )
                try:
                    url = _submit_redacted_fallback_issue(
                        finding, target_repo, project_path,
                    )
                except Exception as e2:
                    print(
                        f"[audit] Fallback issue also failed for "
                        f"'{title}': {e2}",
                        file=sys.stderr,
                    )
                    continue
            else:
                print(
                    f"[audit] Failed to create issue '{title}': {e}",
                    file=sys.stderr,
                )
                continue

        url = url.strip() if url else ""
        if url:
            issue_urls.append(url)
            created_count += 1
            if notify_fn:
                notify_fn(f"  \U0001f517 {url}")

    return IssueCreationResult(
        urls=issue_urls,
        created=created_count,
        reused=reused_count,
    )


def _submit_pvrs_report(
    finding: AuditFinding,
    ecosystem: str,
    package_name: str,
    target_repo: Optional[str],
    project_path: str,
) -> str:
    """Submit a single finding as a PVRS report. Returns the advisory URL."""
    from app.github import security_advisory_report

    description = _build_advisory_description(finding)
    return security_advisory_report(
        summary=f"Security: {finding.title}",
        description=description,
        severity=finding.severity,
        ecosystem=ecosystem,
        package_name=package_name,
        repo=target_repo,
        cwd=project_path,
    )


def _submit_public_issue(
    finding: AuditFinding,
    target_repo: Optional[str],
    project_path: str,
    title_prefix: str = "",
) -> str:
    """Create a public GitHub issue for a finding. Returns the issue URL."""
    from app.github import issue_create

    return issue_create(
        title=f"{title_prefix}{finding.title}",
        body=_build_issue_body(finding),
        repo=target_repo,
        cwd=project_path,
    )


def _submit_redacted_fallback_issue(
    finding: AuditFinding,
    target_repo: Optional[str],
    project_path: str,
) -> str:
    """Create a redacted public issue when PVRS submission fails.

    Omits exploit details to avoid leaking vulnerability information publicly.
    The issue serves as a placeholder directing maintainers to investigate
    via private channels.
    """
    from app.github import issue_create

    redacted_body = (
        "A security finding was identified during an automated audit but "
        "could not be submitted via Private Vulnerability Reporting (PVRS).\n\n"
        f"**Severity**: {finding.severity}\n"
        f"**Category**: {finding.category}\n\n"
        "Details have been withheld from this public issue to prevent "
        "disclosure of exploitable vulnerabilities. Please review the audit "
        "logs or contact the security team for full details.\n\n"
        "---\n"
        "\U0001f916 Created by K\u014dan from audit session"
    )

    return issue_create(
        title=f"[Security] {finding.severity} finding — details withheld (PVRS unavailable)",
        body=redacted_body,
        repo=target_repo,
        cwd=project_path,
    )


# ---------------------------------------------------------------------------
# Report saving
# ---------------------------------------------------------------------------

def _save_audit_report(
    instance_dir: Path,
    project_name: str,
    findings: List[AuditFinding],
    issue_urls: List[str],
    report_name: str = "audit",
) -> Path:
    """Save the audit summary to the project's learnings directory."""
    from datetime import datetime as _dt

    learnings_dir = instance_dir / "memory" / "projects" / project_name
    learnings_dir.mkdir(parents=True, exist_ok=True)

    report_path = learnings_dir / f"{report_name}.md"

    timestamp = _dt.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"<!-- Last audit: {timestamp} -->",
        f"<!-- Findings: {len(findings)} -->",
        f"",
        f"# Audit Report — {project_name}",
        f"",
    ]

    for i, finding in enumerate(findings):
        url = issue_urls[i] if i < len(issue_urls) else "no issue created"
        # Annotate channel: PVRS reports have GHSA IDs or advisory URLs
        if "/advisories/" in url or url.startswith("GHSA"):
            channel = "private"
        else:
            channel = ""
        suffix = f" ({channel})" if channel else ""
        lines.append(
            f"- [{finding.severity}] {finding.title} "
            f"(`{finding.location}`) — {url}{suffix}"
        )

    lines.append("")
    report_path.write_text("\n".join(lines))
    return report_path


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_audit(
    project_path: str,
    project_name: str,
    instance_dir: str,
    extra_context: str = "",
    max_issues: int = DEFAULT_MAX_ISSUES,
    notify_fn=None,
    skill_dir: Optional[Path] = None,
    report_name: str = "audit",
    pvrs_mode: str = "auto",
    pvrs_threshold: str = "high",
) -> Tuple[bool, str]:
    """Execute a codebase audit on a project.

    Args:
        project_path: Local path to the project.
        project_name: Project name for labeling.
        instance_dir: Path to instance directory.
        extra_context: Optional focus guidance from the user.
        max_issues: Maximum number of findings to create issues for.
        notify_fn: Optional callback for progress notifications.
        skill_dir: Optional path to the audit skill directory for prompts.
        report_name: Base name for the saved report file (default: "audit").
        pvrs_mode: PVRS routing mode (``"auto"``, ``"true"``, ``"false"``).
        pvrs_threshold: Minimum severity for PVRS routing (default ``"high"``).

    Returns:
        (success, summary) tuple.
    """
    if notify_fn is None:
        from app.notify import send_telegram
        notify_fn = send_telegram

    instance_path = Path(instance_dir)

    # Step 1: Build prompt
    context_hint = f" (focus: {extra_context})" if extra_context else ""
    notify_fn(f"\U0001f50e Auditing {project_name}{context_hint}...")
    prompt = build_audit_prompt(
        project_name, extra_context, skill_dir=skill_dir,
        max_issues=max_issues,
    )

    # Step 2: Run Claude audit (read-only)
    try:
        raw_output = _run_claude_audit(prompt, project_path)
    except RuntimeError as e:
        return False, f"Audit failed: {e}"

    if not raw_output:
        return False, f"Audit produced no output for {project_name}."

    # Step 3: Parse findings
    findings = parse_findings(raw_output)
    if not findings:
        notify_fn(f"\u2705 Audit of {project_name} found no actionable issues.")
        return True, "Audit completed — no findings."

    # Step 4: Enforce max_issues limit (keep top N by severity)
    original_count = len(findings)
    findings = prioritize_findings(findings, max_issues)
    if len(findings) < original_count:
        notify_fn(
            f"\U0001f4cb Found {original_count} issue(s), "
            f"keeping top {len(findings)}. Creating GitHub issues..."
        )
    else:
        notify_fn(
            f"\U0001f4cb Found {len(findings)} issue(s). "
            f"Creating GitHub issues..."
        )

    # Step 5: Create GitHub issues (or PVRS reports for security audits).
    # Findings whose titles match an already-open audit issue on the
    # repo are skipped \u2014 the existing URL is reused so a second run
    # doesn't pile up duplicate tickets for the same problem.
    result = create_issues(
        findings, project_path, notify_fn=notify_fn,
        pvrs_mode=pvrs_mode, pvrs_threshold=pvrs_threshold,
    )

    # Step 6: Save report
    report_path = _save_audit_report(
        instance_path, project_name, findings, result.urls,
        report_name=report_name,
    )

    # Build summary
    if result.reused:
        issue_summary = (
            f"{result.created} new, {result.reused} already tracked"
        )
    else:
        issue_summary = f"{result.created} GitHub issues created"
    summary = (
        f"Audit complete: {len(findings)} findings, "
        f"{issue_summary}. "
        f"Report saved to {report_path.name}."
    )
    notify_fn(f"\u2705 {summary}")

    return True, summary


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv=None):
    """CLI entry point for audit_runner."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Audit a project codebase and create GitHub issues."
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
        help=f"Maximum number of findings to create issues for (default: {DEFAULT_MAX_ISSUES})",
    )
    cli_args = parser.parse_args(argv)

    # Context from file takes precedence
    context = cli_args.context
    if cli_args.context_file:
        try:
            context = Path(cli_args.context_file).read_text(encoding="utf-8").strip()
        except OSError as e:
            print(f"Warning: could not read context file: {e}", file=sys.stderr)

    skill_dir = Path(__file__).resolve().parent

    success, summary = run_audit(
        project_path=cli_args.project_path,
        project_name=cli_args.project_name,
        instance_dir=cli_args.instance_dir,
        extra_context=context,
        max_issues=cli_args.max_issues,
        skill_dir=skill_dir,
    )
    print(summary)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
