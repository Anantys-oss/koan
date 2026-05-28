"""Per-project skill metrics tracking.

Records plan-review outcomes and fix/implement PR results to
``memory/projects/{name}/skill-metrics.md`` as append-only markdown table rows.
Provides summary helpers consumed by ``/status`` and deep-research prompts.
"""

from __future__ import annotations

import contextlib
import fcntl
import sys
from datetime import datetime, timedelta
from pathlib import Path


# ---------------------------------------------------------------------------
# File layout
# ---------------------------------------------------------------------------

_TABLE_HEADER = (
    "| Date | Skill | Outcome | Rounds | Detail |\n"
    "| ---- | ----- | ------- | ------ | ------ |"
)

_METRICS_FILENAME = "skill-metrics.md"


def _metrics_path(instance_dir: str, project_name: str) -> Path:
    return Path(instance_dir) / "memory" / "projects" / project_name / _METRICS_FILENAME


def _ensure_table(path: Path) -> None:
    """Create the metrics file with header if it doesn't exist."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# Skill Metrics\n\n{_TABLE_HEADER}\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Recording helpers
# ---------------------------------------------------------------------------

def record_plan_metric(
    instance_dir: str,
    project_name: str,
    approved: bool,
    rounds: int,
    issues_summary: str = "",
) -> None:
    """Append a plan-review outcome row.

    Args:
        instance_dir: Path to instance directory.
        project_name: Project name.
        approved: Whether the plan was approved.
        rounds: Number of review rounds completed.
        issues_summary: Truncated issues string (max 80 chars stored).
    """
    path = _metrics_path(instance_dir, project_name)
    _ensure_table(path)

    iso = datetime.now().strftime("%Y-%m-%d")
    outcome = "APPROVED" if approved else "REJECTED"
    detail = _sanitize(issues_summary, max_len=80)

    row = f"| {iso} | plan | {outcome} | {rounds} | {detail} |"
    try:
        with open(path, "a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(row + "\n")
    except OSError as e:
        print(f"[skill_metrics] Failed to write plan metric: {e}", file=sys.stderr)


def record_pr_metric(
    instance_dir: str,
    project_name: str,
    skill_type: str,
    pr_url: str = "",
    ci_status: str = "",
) -> None:
    """Append a fix/implement PR outcome row.

    Args:
        instance_dir: Path to instance directory.
        project_name: Project name.
        skill_type: e.g. "fix", "implement", "review".
        pr_url: PR URL if created.
        ci_status: CI result — "pass", "fail", "pending", "none".
    """
    path = _metrics_path(instance_dir, project_name)
    _ensure_table(path)

    iso = datetime.now().strftime("%Y-%m-%d")
    outcome = f"CI:{ci_status}" if ci_status else "submitted"
    detail = _sanitize(pr_url, max_len=80)

    row = f"| {iso} | {skill_type} | {outcome} | - | {detail} |"
    try:
        with open(path, "a", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(row + "\n")
    except OSError as e:
        print(f"[skill_metrics] Failed to write PR metric: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Reading / summarizing
# ---------------------------------------------------------------------------

def read_metrics(
    instance_dir: str,
    project_name: str,
    days: int = 30,
) -> list[dict]:
    """Read metric rows for a project, filtered to recent N days.

    Returns list of dicts with keys: date, skill, outcome, rounds, detail.
    """
    path = _metrics_path(instance_dir, project_name)
    if not path.exists():
        return []

    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = []

    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.startswith("| 2"):  # table rows start with "| 20xx-..."
                continue
            parts = [p.strip() for p in line.split("|")]
            # parts: ['', date, skill, outcome, rounds, detail, '']
            if len(parts) < 7:
                continue
            date_str = parts[1]
            if date_str < cutoff:
                continue
            rows.append({
                "date": date_str,
                "skill": parts[2],
                "outcome": parts[3],
                "rounds": parts[4],
                "detail": parts[5],
            })
    except (OSError, UnicodeDecodeError) as e:
        print(f"[skill_metrics] Failed to read metrics: {e}", file=sys.stderr)

    return rows


def compute_summary(
    instance_dir: str,
    project_name: str,
    days: int = 30,
) -> dict:
    """Compute aggregated metrics for a project.

    Returns dict with:
        plan_total, plan_approved, plan_approval_rate,
        plan_avg_rounds, pr_total, pr_ci_pass, pr_ci_fail,
        pr_ci_pass_rate.
    """
    rows = read_metrics(instance_dir, project_name, days=days)

    plan_rows = [r for r in rows if r["skill"] == "plan"]
    pr_rows = [r for r in rows if r["skill"] in ("fix", "implement")]

    plan_approved = sum(1 for r in plan_rows if r["outcome"] == "APPROVED")
    plan_total = len(plan_rows)

    # Parse rounds for average
    rounds_values = []
    for r in plan_rows:
        with contextlib.suppress(ValueError, TypeError):
            rounds_values.append(int(r["rounds"]))

    pr_ci_pass = sum(1 for r in pr_rows if r["outcome"] == "CI:pass")
    pr_ci_fail = sum(1 for r in pr_rows if r["outcome"] == "CI:fail")
    pr_total = len(pr_rows)

    return {
        "plan_total": plan_total,
        "plan_approved": plan_approved,
        "plan_approval_rate": plan_approved / plan_total if plan_total else 0.0,
        "plan_avg_rounds": (
            sum(rounds_values) / len(rounds_values) if rounds_values else 0.0
        ),
        "pr_total": pr_total,
        "pr_ci_pass": pr_ci_pass,
        "pr_ci_fail": pr_ci_fail,
        "pr_ci_pass_rate": pr_ci_pass / pr_total if pr_total else 0.0,
    }


def format_skill_metrics_summary(
    instance_dir: str,
    project_name: str,
    days: int = 30,
) -> str:
    """Format a human-readable skill metrics summary.

    Returns empty string if no data available.
    """
    s = compute_summary(instance_dir, project_name, days=days)
    if s["plan_total"] == 0 and s["pr_total"] == 0:
        return ""

    lines = []
    if s["plan_total"] > 0:
        lines.append(
            f"  Plan reviews: {s['plan_approval_rate']:.0%} approved "
            f"({s['plan_approved']}/{s['plan_total']}), "
            f"avg {s['plan_avg_rounds']:.1f} rounds"
        )
    if s["pr_total"] > 0:
        lines.append(
            f"  PR CI: {s['pr_ci_pass_rate']:.0%} pass "
            f"({s['pr_ci_pass']}/{s['pr_total']})"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _sanitize(text: str, max_len: int = 80) -> str:
    """Sanitize a string for safe embedding in a markdown table cell."""
    # Remove pipe chars and newlines, truncate
    clean = text.replace("|", "/").replace("\n", " ").replace("\r", "").strip()
    if len(clean) > max_len:
        clean = clean[:max_len - 3] + "..."
    return clean
