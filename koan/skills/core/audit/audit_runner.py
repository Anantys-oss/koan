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

import fcntl
import hashlib
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

from app.prompts import load_prompt_or_skill

DEFAULT_MAX_ISSUES = 5

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

_LIMIT_RE = re.compile(r"\blimit=(\d+)\b", re.IGNORECASE)


def extract_limit(text: str, default: int = DEFAULT_MAX_ISSUES) -> Tuple[int, str]:
    """Extract ``limit=N`` from text. Returns ``(limit, cleaned_text)``.

    Shared by all audit-family skill handlers (`/audit`, `/security_audit`,
    `/private_security_audit`) so the parsing logic cannot drift.
    """
    m = _LIMIT_RE.search(text)
    if not m:
        return default, text
    limit = int(m.group(1))
    cleaned = (text[:m.start()] + text[m.end():]).strip()
    cleaned = re.sub(r"  +", " ", cleaned)
    return max(1, limit), cleaned


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
    instance_dir: Optional[str] = None,
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

    security_block = ""
    if instance_dir:
        try:
            from skills.core.audit.security_learnings import build_security_memory_block
            security_block = build_security_memory_block(instance_dir, project_name)
        except Exception as e:
            print(f"[audit_runner] security memory injection failed: {e}", file=sys.stderr)

    return load_prompt_or_skill(
        skill_dir, "audit",
        PROJECT_NAME=project_name,
        EXTRA_CONTEXT=context_block,
        MAX_ISSUES=str(max_issues),
        SECURITY_INTELLIGENCE=security_block,
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

    ``created_entries`` pairs each newly-created issue with its
    originating finding so callers can filter by severity for auto-fix.

    ``local_files`` pairs each high+ finding with its local security
    file path (written regardless of PVRS outcome).
    """

    urls: List[str]
    created: int
    reused: int
    created_entries: Tuple = ()
    local_files: Tuple = ()


_FINGERPRINT_MARKER_RE = re.compile(
    r"<!--\s*koan-audit-id:\s*([0-9a-f]{16})\s*-->",
)


def _compute_finding_fingerprint(finding: AuditFinding) -> str:
    """Return a stable 16-char fingerprint for *finding*.

    Hashes ``location + ':' + category`` (both normalized to lowercase
    with whitespace collapsed). The fingerprint is embedded in the
    issue body when the issue is created and matched on reruns so the
    audit pipeline dedups on a token immune to LLM-generated title
    drift (the original title-equality approach missed reruns when
    Claude rephrased the finding).
    """
    location = " ".join((finding.location or "").lower().split())
    category = " ".join((finding.category or "").lower().split())
    digest = hashlib.sha256(f"{location}:{category}".encode("utf-8")).hexdigest()
    return digest[:16]


def _build_existing_fingerprint_index(
    existing_issues: List[Dict],
) -> Dict[str, str]:
    """Map embedded ``koan-audit-id`` fingerprint -> issue URL.

    Walks the bodies of existing audit issues, extracts the
    ``<!-- koan-audit-id: ... -->`` marker emitted by
    :func:`_build_issue_body`, and indexes the URL by that fingerprint.
    Issues without the marker (pre-fingerprint vintage) are skipped —
    they will not match new findings and will simply be left alone on
    the repo until manually closed.
    """
    index: Dict[str, str] = {}
    for issue in existing_issues:
        body = issue.get("body") or ""
        url = issue.get("url") or ""
        if not body or not url:
            continue
        match = _FINGERPRINT_MARKER_RE.search(body)
        if not match:
            continue
        # First occurrence wins (newest issue listed first by gh).
        index.setdefault(match.group(1), url)
    return index


def _find_existing_match(
    finding: AuditFinding,
    existing_index: Dict[str, str],
) -> Optional[str]:
    """Return the URL of an open audit issue already tracking *finding*.

    Lookup is exact on the fingerprint embedded in the issue body so
    title rewording between runs does not defeat dedup.
    """
    if not existing_index:
        return None
    return existing_index.get(_compute_finding_fingerprint(finding))


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
    """Build a GitHub issue body from a finding.

    Appends a hidden ``<!-- koan-audit-id: ... -->`` marker so future
    audit runs can dedup against this issue even if the LLM rewords
    the title between runs.
    """
    severity_icon = _SEVERITY_LABELS.get(finding.severity, "\u2753")
    effort_label = _EFFORT_LABELS.get(finding.effort, finding.effort)
    fingerprint = _compute_finding_fingerprint(finding)

    lines = [
        "## Problem",
        "",
        f"{finding.problem}",
        "",
        "## Why This Matters",
        "",
        f"{finding.why}",
        "",
        "## Suggested Fix",
        "",
        f"{finding.suggested_fix}",
        "",
        "## Details",
        "",
        "| | |",
        "|---|---|",
        f"| **Severity** | {severity_icon} {finding.severity.capitalize()} |",
        f"| **Category** | {finding.category} |",
        f"| **Location** | `{finding.location}` |",
        f"| **Effort** | {effort_label} |",
        "",
        "---",
        "\U0001f916 Created by K\u014dan from audit session",
        f"<!-- koan-audit-id: {fingerprint} -->",
    ]
    return "\n".join(lines)


def _build_advisory_description(finding: AuditFinding) -> str:
    """Build a PVRS advisory description from a finding.

    Similar to ``_build_issue_body()`` but formatted for the PVRS description
    field (pure markdown, no table metadata — structured fields go in the
    JSON payload).
    """
    lines = [
        "## Problem",
        "",
        f"{finding.problem}",
        "",
        "## Why This Matters",
        "",
        f"{finding.why}",
        "",
        "## Suggested Fix",
        "",
        f"{finding.suggested_fix}",
        "",
        f"**Location**: `{finding.location}`",
        f"**Category**: {finding.category}",
        "",
        "---",
        "\U0001f916 Reported by K\u014dan security audit",
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
    project_name: str = "",
    instance_dir: str = "",
) -> IssueCreationResult:
    """Create GitHub issues (or PVRS reports) for each finding.

    Before opening a new issue, the repo's currently-open audit issues
    are fetched and findings whose fingerprint (``location + category``)
    matches an existing issue body are skipped — the existing URL is
    reused so reruns of the audit do not duplicate previously-tracked
    findings even when the LLM rephrases the title.

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
    from app.issue_tracker import tracker_provider

    # PVRS and existing-issue lookup are GitHub-only — skip the gh-backed
    # calls entirely when the project routes to a non-GitHub tracker (e.g.
    # Jira). Without this, audit_runner shells out to gh for a repo that
    # may not exist locally, just to discard the result.
    is_github_tracker = (
        tracker_provider(project_name, project_path) == "github"
        if project_name else True
    )

    target_repo = ""
    pvrs_available = False
    existing_index: Dict[str, str] = {}

    if is_github_tracker:
        target_repo = resolve_target_repo(
            project_path, project_name=project_name,
        )

        # Determine PVRS availability
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
        existing_index = _build_existing_fingerprint_index(
            list_open_audit_issues(repo=target_repo, cwd=project_path)
        )

    ecosystem = detect_ecosystem(project_path) if pvrs_available else "other"
    # Derive a package name from the project directory
    package_name = Path(project_path).name

    issue_urls = []
    created_count = 0
    reused_count = 0
    created_entries: List[Tuple[AuditFinding, str]] = []
    local_files: List[Tuple[AuditFinding, Path]] = []

    for i, finding in enumerate(findings, 1):
        title = finding.title
        is_high_severity = _should_use_pvrs(finding.severity, pvrs_threshold)
        use_pvrs = pvrs_available and is_high_severity

        # High+ severity: attempt PVRS then write local file
        if is_high_severity:
            if notify_fn:
                notify_fn(
                    f"  \U0001f512 {i}/{len(findings)}: {title}"
                )

            advisory_url = ""
            pvrs_status = "disabled"

            if use_pvrs:
                try:
                    advisory_url = _submit_pvrs_report(
                        finding, ecosystem, package_name,
                        target_repo, project_path,
                    )
                    advisory_url = advisory_url.strip() if advisory_url else ""
                    if advisory_url:
                        pvrs_status = "submitted"
                        issue_urls.append(advisory_url)
                        created_count += 1
                        created_entries.append((finding, advisory_url))
                    else:
                        pvrs_status = "failed"
                except Exception as e:
                    pvrs_status = "failed"
                    print(
                        f"[audit] PVRS failed for '{title}': {repr(e)}",
                        file=sys.stderr,
                    )

            # Always write local file for high+ findings
            if instance_dir:
                try:
                    file_path = _write_local_finding(
                        finding, project_name, instance_dir,
                        pvrs_status=pvrs_status,
                        advisory_url=advisory_url,
                    )
                except Exception as e:
                    print(
                        f"[audit] Failed to write local finding for "
                        f"'{title}': {repr(e)}",
                        file=sys.stderr,
                    )
                    continue
                local_files.append((finding, file_path))
                relative = f"security/{project_name}/{file_path.name}"
                if notify_fn:
                    notify_fn(
                        f"  \U0001f4c4 {relative}\n"
                        f"  \U0001f4a1 Suggested: /fix {project_name} "
                        f"Understand and fix the issue described by {relative}"
                    )
            elif pvrs_status != "submitted":
                print(
                    f"[audit] No instance_dir configured — cannot store "
                    f"high-severity finding '{title}' locally",
                    file=sys.stderr,
                )

            continue

        # Public issue path (medium/low, or high fallback without instance_dir)
        # Dedup: skip if fingerprint matches an already-open audit issue.
        existing_url = _find_existing_match(finding, existing_index)
        if existing_url:
            reused_count += 1
            issue_urls.append(existing_url)
            if notify_fn:
                notify_fn(
                    f"  \u21a9\ufe0f {i}/{len(findings)}: "
                    f"already tracked \u2014 {existing_url}"
                )
            continue

        if notify_fn:
            notify_fn(
                f"  \U0001f4dd issue {i}/{len(findings)}: {title}"
            )

        try:
            url = _submit_public_issue(
                finding, project_name, project_path,
            )
        except Exception as e:
            print(
                f"[audit] Failed to create issue '{title}': {e}",
                file=sys.stderr,
            )
            continue

        url = url.strip() if url else ""
        if url:
            issue_urls.append(url)
            created_count += 1
            created_entries.append((finding, url))
            if notify_fn:
                notify_fn(f"  \U0001f517 {url}")

    return IssueCreationResult(
        urls=issue_urls,
        created=created_count,
        reused=reused_count,
        created_entries=tuple(created_entries),
        local_files=tuple(local_files),
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
    project_name: str,
    project_path: str,
    title_prefix: str = "",
) -> str:
    """Create a public tracker issue for a finding. Returns the issue URL."""
    from app.issue_tracker import create_issue

    return create_issue(
        project_name=project_name,
        project_path=project_path,
        title=f"{title_prefix}{finding.title}",
        body=_build_issue_body(finding),
    )


def _slugify_finding_title(title: str) -> str:
    """Convert a finding title to a filesystem-safe slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:60]


def _write_local_finding(
    finding: AuditFinding,
    project_name: str,
    instance_dir: str,
    pvrs_status: str = "disabled",
    advisory_url: str = "",
) -> Path:
    """Write a security finding to a local markdown file.

    Always called for high+ severity findings regardless of PVRS outcome.
    The file serves as the local source of truth for security findings.

    Returns:
        Path to the written file.
    """
    import os

    from app.utils import atomic_write

    now = datetime.now()
    today = now.strftime("%Y%m%d")
    slug = _slugify_finding_title(finding.title)
    title_hash = hashlib.sha256(finding.title.encode()).hexdigest()[:6]
    filename = f"{today}.{finding.severity}.{slug}.{title_hash}.md"

    security_dir = Path(instance_dir) / "security" / project_name
    os.makedirs(security_dir, exist_ok=True)

    file_path = security_dir / filename

    advisory_line = advisory_url if advisory_url else "—"
    content = (
        f"# {finding.title}\n\n"
        f"| Field | Value |\n"
        f"|-------|-------|\n"
        f"| Severity | {finding.severity} |\n"
        f"| Category | {finding.category} |\n"
        f"| Location | `{finding.location}` |\n"
        f"| Detected | {now.strftime('%Y-%m-%d')} |\n"
        f"| PVRS | {pvrs_status} |\n"
        f"| Advisory | {advisory_line} |\n\n"
        f"## Problem\n\n{finding.problem}\n\n"
        f"## Why This Matters\n\n{finding.why}\n\n"
        f"## Suggested Fix\n\n{finding.suggested_fix}\n"
    )

    atomic_write(file_path, content)
    return file_path


# ---------------------------------------------------------------------------
# Auto-fix mission queueing
# ---------------------------------------------------------------------------

AUTO_FIX_CAP = 3
AUTO_FIX_DEFAULT_THRESHOLD = "high"  # critical + high


def severity_at_or_above(severity: str, threshold: str) -> bool:
    """Return True if *severity* is at or above *threshold*.

    Uses the same ``_SEVERITY_ORDER`` as finding prioritization. Both an
    unknown severity *and* an unknown threshold fail closed (return False):
    if we don't know how the finding ranks, we don't auto-fix it; if we
    don't know what the operator meant by ``threshold=foo``, we also don't
    auto-fix it. The previous implementation defaulted both to 99, which
    made every unknown threshold accept every unknown severity — the worst
    of both worlds.
    """
    if severity not in _SEVERITY_ORDER or threshold not in _SEVERITY_ORDER:
        return False
    return _SEVERITY_ORDER[severity] <= _SEVERITY_ORDER[threshold]


def queue_auto_fix_missions(
    created_entries: Tuple,
    project_name: str,
    instance_dir: str,
    threshold: str = AUTO_FIX_DEFAULT_THRESHOLD,
    notify_fn=None,
) -> int:
    """Queue ``/fix`` missions for newly-created audit issues.

    Filters *created_entries* (finding, url) pairs by severity and
    queues at most :data:`AUTO_FIX_CAP` missions.

    PVRS-routed findings (advisory URLs containing ``/advisories/``)
    are skipped — they cannot be linked as public fix targets.

    Returns the number of missions queued.
    """
    from app.utils import insert_pending_mission

    queued = 0

    for finding, url in created_entries:
        if queued >= AUTO_FIX_CAP:
            break

        if not severity_at_or_above(finding.severity, threshold):
            continue

        # Skip PVRS advisories — they can't be fixed via /fix <url>
        if "/advisories/" in url:
            continue

        mission_text = f"/fix {url}"
        inserted = insert_pending_mission(mission_text, project_name)
        if inserted:
            queued += 1

    if queued and notify_fn:
        cap_note = f" (cap: {AUTO_FIX_CAP})" if queued >= AUTO_FIX_CAP else ""
        notify_fn(
            f"  \U0001f527 Auto-fix: queued {queued} /fix mission(s) "
            f"for {threshold}+ severity{cap_note}"
        )

    return queued


# ---------------------------------------------------------------------------
# Report saving
# ---------------------------------------------------------------------------

def _write_findings_to_journal(
    instance_dir: Path,
    project_name: str,
    findings: List[AuditFinding],
    extra_context: str = "",
) -> Path:
    """Append audit findings to today's project journal file.

    Used by ``/private_security_audit`` so vulnerability details never leave
    the local instance. Writes a structured markdown section so it can be
    distinguished from regular journal entries.
    """
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    timestamp = now.strftime("%H:%M:%S")
    journal_dir = instance_dir / "journal" / today
    journal_dir.mkdir(parents=True, exist_ok=True)
    journal_path = journal_dir / f"{project_name}.md"

    lines = [
        "",
        f"## \U0001f512 Private Security Audit — {today} {timestamp}",
        "",
        "*Findings recorded locally only — not posted to GitHub.*",
        "",
    ]
    if extra_context:
        lines.extend([f"**Focus:** {extra_context}", ""])

    for i, finding in enumerate(findings, 1):
        severity_icon = _SEVERITY_LABELS.get(finding.severity, "❓")
        lines.extend([
            f"### {i}. {severity_icon} {finding.severity.capitalize()} — {finding.title}",
            "",
            f"- **Location:** `{finding.location}`",
            f"- **Category:** {finding.category}",
            f"- **Effort:** {finding.effort}",
            "",
            "**Problem**",
            "",
            finding.problem,
            "",
            "**Why it matters**",
            "",
            finding.why,
            "",
            "**Suggested fix**",
            "",
            finding.suggested_fix,
            "",
        ])

    with open(journal_path, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write("\n".join(lines) + "\n")
            f.flush()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)

    return journal_path


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
        "",
        f"# Audit Report — {project_name}",
        "",
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
    journal_only: bool = False,
    auto_fix_severity: Optional[str] = None,
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
        journal_only: When True, skip GitHub issue / PVRS creation entirely
            and write findings to today's journal file instead. Used by
            ``/private_security_audit`` to keep sensitive findings off
            public GitHub.
        auto_fix_severity: When set, queue ``/fix`` missions for newly-created
            issues at or above this severity (e.g. ``"high"`` queues critical
            and high). ``None`` disables auto-fix (default).

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
        max_issues=max_issues, instance_dir=instance_dir,
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

    # Step 5: Output findings -- either GitHub issues or journal-only.
    # For GitHub: findings whose fingerprint matches an already-open audit
    # issue on the repo are skipped \u2014 the existing URL is reused so a
    # second run doesn't pile up duplicate tickets for the same problem.
    if journal_only:
        journal_path = _write_findings_to_journal(
            instance_path, project_name, findings, extra_context,
        )
        result = IssueCreationResult(urls=[], created=0, reused=0)
        notify_fn(
            f"\U0001f4d3 Wrote {len(findings)} finding(s) to journal: "
            f"{journal_path.name}"
        )
    else:
        result = create_issues(
            findings, project_path, notify_fn=notify_fn,
            pvrs_mode=pvrs_mode, pvrs_threshold=pvrs_threshold,
            project_name=project_name, instance_dir=instance_dir,
        )

    # Step 6: Auto-fix — queue /fix missions for high-severity new issues
    auto_fix_count = 0
    if auto_fix_severity and result.created_entries:
        auto_fix_count = queue_auto_fix_missions(
            result.created_entries,
            project_name,
            instance_dir,
            threshold=auto_fix_severity,
            notify_fn=notify_fn,
        )

    # Step 7: Save report
    report_path = _save_audit_report(
        instance_path, project_name, findings, result.urls,
        report_name=report_name,
    )

    # Step 7: Extract security learnings (best-effort, never fails the audit)
    try:
        from skills.core.audit.security_learnings import extract_security_learnings
        extract_security_learnings(raw_output, project_name, instance_dir, project_path)
    except (subprocess.CalledProcessError, RuntimeError) as e:
        print(f"[audit_runner] security learning extraction failed: {e}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001 — intentional catch-all
        print(f"[audit_runner] security learning extraction error: {e}", file=sys.stderr)

    # Build summary
    if journal_only:
        summary = (
            f"Private audit complete: {len(findings)} findings written to journal. "
            f"Report saved to {report_path.name} (no GitHub issues created)."
        )
    else:
        parts = []
        if result.created and result.reused:
            parts.append(f"{result.created} new")
        elif result.created:
            parts.append(f"{result.created} GitHub issues created")
        if result.reused:
            parts.append(f"{result.reused} already tracked")
        if result.local_files:
            parts.append(f"{len(result.local_files)} local security files")
        issue_summary = ", ".join(parts) if parts else "no issues created"
        fix_summary = f", {auto_fix_count} auto-fix queued" if auto_fix_count else ""
        summary = (
            f"Audit complete: {len(findings)} findings, "
            f"{issue_summary}{fix_summary}. "
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
    parser.add_argument(
        "--journal-only", action="store_true",
        help="Skip GitHub issue creation; write findings to journal only",
    )
    parser.add_argument(
        "--auto-fix", nargs="?", const=AUTO_FIX_DEFAULT_THRESHOLD,
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

    skill_dir = Path(__file__).resolve().parent

    success, summary = run_audit(
        project_path=cli_args.project_path,
        project_name=cli_args.project_name,
        instance_dir=cli_args.instance_dir,
        extra_context=context,
        max_issues=cli_args.max_issues,
        skill_dir=skill_dir,
        journal_only=cli_args.journal_only,
        auto_fix_severity=cli_args.auto_fix,
    )
    print(summary)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
