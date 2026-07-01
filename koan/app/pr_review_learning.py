"""Kōan — PR review learning for autonomous alignment.

Extracts actionable lessons from human PR reviews (comments, approvals,
rejections, closures) and persists them to the project's learnings.md.

The PR feedback system (pr_feedback.py) tracks *merge velocity* — how fast
PRs get merged by category. This module goes deeper: it reads the actual
review comments and actions to learn *what* the human values, critiques,
or rejects.

Architecture:
1. Fetch: GitHub API via gh CLI (review comments, states, closed PRs)
2. Analyze: Claude CLI (lightweight model) parses raw feedback into lessons
3. Persist: New lessons are appended to memory/projects/{name}/learnings.md

The learnings.md file is already consumed by deep_research.py,
prompt_builder.py, and format_outbox.py — so lessons written here
are automatically surfaced to the agent without additional wiring.
"""

import hashlib
import json
import logging
import re
import subprocess
import sys
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

log = logging.getLogger(__name__)


def fetch_pr_reviews(
    project_path: str,
    days: int = 30,
    limit: int = 30,
) -> List[dict]:
    """Fetch recent koan/* PRs with their review data.

    For each PR, fetches:
    - Basic info (number, title, state, branch)
    - Reviews (state, body, author)
    - Review comments (body, path)

    Args:
        project_path: Path to the git repo.
        days: Look back this many days.
        limit: Maximum PRs to fetch.

    Returns:
        List of enriched PR dicts with review data.
    """
    try:
        from app.github import run_gh
    except ImportError:
        return []

    try:
        from app.config import get_branch_prefix
        prefix = get_branch_prefix()
    except Exception as e:
        print(f"[pr_review_learning] branch prefix lookup failed: {e}", file=sys.stderr)
        prefix = "koan/"

    # Fetch all non-open PRs in a single call to avoid double-fetching
    try:
        raw = run_gh(
            "pr", "list",
            "--state", "all",
            "--limit", str(limit),
            "--json", "number,title,createdAt,mergedAt,closedAt,headRefName,state",
            cwd=project_path,
            timeout=15,
        )
        prs = json.loads(raw)
    except Exception as e:
        print(f"[pr_review_learning] Failed to fetch PRs: {e}", file=sys.stderr)
        prs = []

    # Filter to koan/* branches, non-open, and recent PRs
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    filtered = []
    for pr in prs:
        # Skip open PRs — only merged/closed have review learnings
        state = (pr.get("state") or "").upper()
        if state == "OPEN":
            continue

        branch = pr.get("headRefName", "")
        if not branch.startswith(prefix):
            continue

        # Check date (merged or closed)
        date_str = pr.get("mergedAt") or pr.get("closedAt") or pr.get("createdAt", "")
        pr_date = _parse_iso(date_str)
        if pr_date and pr_date < cutoff:
            continue

        filtered.append(pr)

    # Enrich each PR with reviews and comments
    enriched = []
    for pr in filtered[:limit]:
        num = pr["number"]
        reviews = _fetch_reviews_for_pr(project_path, num)
        comments = _fetch_review_comments_for_pr(project_path, num)

        pr["reviews"] = reviews
        pr["review_comments"] = comments
        pr["was_merged"] = bool(pr.get("mergedAt"))

        if not pr["was_merged"]:
            pr["issue_comments"] = _fetch_issue_comments_for_pr(project_path, num)
        else:
            pr["issue_comments"] = []

        enriched.append(pr)

    return enriched


def _fetch_gh_jsonl(
    project_path: str,
    endpoint: str,
    jq_filter: str,
    pr_number: int,
    label: str,
) -> List[dict]:
    """Fetch a GitHub API endpoint and parse newline-delimited JSON.

    Shared helper for review and comment fetching — handles the run_gh call,
    JSONL parsing, and error handling in one place.

    Args:
        project_path: Path to the git repository.
        endpoint: API endpoint template (use {owner}/{repo} placeholders).
        jq_filter: jq expression to reshape each item.
        pr_number: PR number (for error messages).
        label: Human-readable label for error context (e.g. "reviews").

    Returns:
        List of parsed JSON objects, or empty list on failure.
    """
    try:
        from app.github import run_gh
        # --paginate follows GitHub's Link headers so PRs with >30 reviews or
        # comments aren't silently truncated at the default page size. gh runs
        # --jq per page, so the JSONL output concatenates cleanly across pages.
        raw = run_gh(
            "api", endpoint, "--paginate", "--jq", jq_filter,
            cwd=project_path, timeout=30,
        )
        if not raw.strip():
            return []
        results = []
        for line in raw.strip().split("\n"):
            line = line.strip()
            if line:
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    log.warning("Malformed JSON in %s for PR #%d: %s", label, pr_number, line)
        return results
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        print(f"[pr_review_learning] {label.capitalize()} fetch failed for #{pr_number}: {e}",
              file=sys.stderr)
        return []


def _fetch_reviews_for_pr(project_path: str, pr_number: int) -> List[dict]:
    """Fetch review submissions for a single PR."""
    return _fetch_gh_jsonl(
        project_path,
        f"repos/{{owner}}/{{repo}}/pulls/{pr_number}/reviews",
        ".[].{state: .state, body: .body, user: .user.login}",
        pr_number,
        "reviews",
    )


def _fetch_review_comments_for_pr(project_path: str, pr_number: int) -> List[dict]:
    """Fetch inline review comments for a single PR."""
    return _fetch_gh_jsonl(
        project_path,
        f"repos/{{owner}}/{{repo}}/pulls/{pr_number}/comments",
        ".[].{body: .body, path: .path, user: .user.login}",
        pr_number,
        "review comments",
    )


def _fetch_issue_comments_for_pr(project_path: str, pr_number: int) -> List[dict]:
    """Fetch issue-thread comments for a PR (GitHub treats PRs as issues)."""
    return _fetch_gh_jsonl(
        project_path,
        f"repos/{{owner}}/{{repo}}/issues/{pr_number}/comments",
        ".[].{body: .body, user: .user.login, created_at: .created_at}",
        pr_number,
        "issue comments",
    )


def format_reviews_for_analysis(prs: List[dict]) -> str:
    """Format enriched PR data as text for Claude to analyze.

    Produces a structured summary of each PR with its reviews and comments,
    suitable as input to the analysis prompt.

    Args:
        prs: List of enriched PR dicts from fetch_pr_reviews().

    Returns:
        Formatted text string, or empty string if no reviews to analyze.
    """
    if not prs:
        return ""

    sections = []
    for pr in prs:
        status = "MERGED" if pr.get("was_merged") else "CLOSED (not merged)"
        header = f"## PR #{pr['number']}: {pr.get('title', '')} [{status}]"
        lines = [header]

        for review in pr.get("reviews", []):
            body = (review.get("body") or "").strip()
            state = review.get("state", "")
            user = review.get("user", "")
            if body:
                lines.append(f"  Review ({state}) by {user}: {body}")
            elif state in ("APPROVED", "CHANGES_REQUESTED"):
                lines.append(f"  Review ({state}) by {user}: [no comment]")

        for comment in pr.get("review_comments", []):
            body = (comment.get("body") or "").strip()
            path = comment.get("path", "")
            user = comment.get("user", "")
            if body:
                lines.append(f"  Inline on {path} by {user}: {body}")

        if not pr.get("was_merged"):
            for comment in pr.get("issue_comments", []):
                body = (comment.get("body") or "").strip()
                user = comment.get("user", "")
                if body:
                    lines.append(f"  Comment by {user}: {body}")

        # Only include PRs that have actual review content
        if len(lines) > 1:
            sections.append("\n".join(lines))

    return "\n\n".join(sections)


def analyze_reviews_with_cli(
    review_text: str,
    project_path: str,
) -> str:
    """Use Claude CLI (lightweight model) to extract lessons from review text.

    Args:
        review_text: Formatted review text from format_reviews_for_analysis().
        project_path: Path to the git repo (used as cwd for CLI).

    Returns:
        Markdown bullet list of lessons, or empty string on failure.
    """
    from app.cli_provider import build_full_command
    from app.config import get_model_config
    from app.prompts import load_prompt

    prompt = load_prompt("review-learning", REVIEW_DATA=review_text)
    models = get_model_config()

    cmd = build_full_command(
        prompt=prompt,
        allowed_tools=[],
        model=models.get("lightweight", "haiku"),
        fallback=models.get("fallback", "sonnet"),
        max_turns=1,
    )

    from app.cli_exec import run_cli_with_retry

    try:
        result = run_cli_with_retry(
            cmd,
            capture_output=True, text=True,
            timeout=60, cwd=project_path,
        )
        if result.returncode != 0:
            print(
                f"[pr_review_learning] CLI analysis failed: {result.stderr[:200]}",
                file=sys.stderr,
            )
            return ""
        return result.stdout.strip()
    except Exception as e:
        print(f"[pr_review_learning] CLI analysis error: {e}", file=sys.stderr)
        return ""


def _compute_review_hash(prs: List[dict]) -> str:
    """Compute a stable hash of review data to detect changes.

    Uses PR numbers + review/comment bodies to produce a fingerprint.
    If the hash hasn't changed since last run, we skip re-analysis.
    """
    parts = []
    for pr in sorted(prs, key=lambda p: p.get("number", 0)):
        parts.append(str(pr.get("number", "")))
        parts.extend(review.get("body") or "" for review in pr.get("reviews", []))
        parts.extend(comment.get("body") or "" for comment in pr.get("review_comments", []))
        parts.extend(comment.get("body") or "" for comment in pr.get("issue_comments", []))
    content = "|".join(parts)
    return hashlib.sha256(content.encode()).hexdigest()


def _get_cache_path(instance_dir: str) -> Path:
    """Get the path to the review learning cache file."""
    return Path(instance_dir) / ".koan-review-learning-hash"


# ─── Consecutive failure tracking ───────────────────────────────────────

_FAILURE_COUNTER_FILE = ".koan-pr-review-analysis-failures"
_FAILURE_ALERT_THRESHOLD = 3


def _get_failure_counter_path(instance_dir: str) -> Path:
    """Get the path to the analysis failure counter file."""
    return Path(instance_dir) / _FAILURE_COUNTER_FILE


def _read_failure_count(instance_dir: str) -> int:
    """Read the current consecutive failure count. Returns 0 if no file."""
    path = _get_failure_counter_path(instance_dir)
    if not path.exists():
        return 0
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return 0


def _increment_failure_count(instance_dir: str) -> int:
    """Increment and persist the consecutive failure counter. Returns new count.

    Note: read-modify-write is not atomic, but this is only called from the
    single-threaded agent loop (learn_from_reviews), so no locking is needed.
    """
    count = _read_failure_count(instance_dir) + 1
    try:
        from app.utils import atomic_write
        atomic_write(_get_failure_counter_path(instance_dir), str(count) + "\n")
    except OSError as e:
        print(f"[pr_review_learning] Failure counter write failed: {e}",
              file=sys.stderr)
    return count


def _reset_failure_count(instance_dir: str) -> None:
    """Reset the failure counter (on successful analysis)."""
    path = _get_failure_counter_path(instance_dir)
    if path.exists():
        try:
            path.unlink()
        except OSError as e:
            log.warning("Failure counter reset failed: %s", e)


def _notify_analysis_failures(instance_dir: str, count: int) -> None:
    """Send outbox alert when consecutive failures reach threshold."""
    if count < _FAILURE_ALERT_THRESHOLD:
        return
    # Only alert on exact threshold to avoid spamming every subsequent failure
    if count != _FAILURE_ALERT_THRESHOLD:
        return
    try:
        from app.utils import append_to_outbox
        from app.notify import NotificationPriority
        outbox_path = Path(instance_dir) / "outbox.md"
        msg = (
            f"⚠️ PR review learning has failed {count} times in a row — "
            f"learnings have stopped accumulating. "
            f"Possible causes: CLI quota, API errors, or no actionable review content.\n"
        )
        append_to_outbox(outbox_path, msg, priority=NotificationPriority.WARNING)
    except (OSError, ImportError) as e:
        print(f"[pr_review_learning] Failed to send failure alert: {e}",
              file=sys.stderr)


def _is_cache_fresh(instance_dir: str, current_hash: str) -> bool:
    """Check if the cached hash matches (no new reviews to process)."""
    cache_path = _get_cache_path(instance_dir)
    if not cache_path.exists():
        return False
    try:
        return cache_path.read_text().strip() == current_hash
    except OSError:
        return False


def _write_cache(instance_dir: str, review_hash: str) -> None:
    """Write the review hash to the cache file."""
    try:
        cache_path = _get_cache_path(instance_dir)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        from app.utils import atomic_write
        atomic_write(cache_path, review_hash + "\n")
    except OSError as e:
        print(f"[pr_review_learning] Cache write failed: {e}", file=sys.stderr)


def _is_write_time_dedup_enabled() -> bool:
    """Return ``memory.write_time_dedup`` from ``config.yaml`` (default True).

    Lookup failures default to True — the dedup pass is the safer
    behaviour, and operators can opt out explicitly via config.
    """
    try:
        from app.utils import load_config
        cfg = load_config() or {}
        mem = cfg.get("memory", {}) or {}
        flag = mem.get("write_time_dedup", True)
        return bool(flag)
    except (ImportError, OSError, ValueError, KeyError, TypeError) as e:
        print(f"[pr_review_learning] dedup config lookup failed: {e}", file=sys.stderr)
        return True


def _dedup_lessons_with_cli(
    new_lessons_text: str,
    existing_content: str,
    project_path: str,
    timeout: int = 15,
) -> Optional[str]:
    """Filter ``new_lessons_text`` against ``existing_content`` via Claude CLI.

    Returns the filtered lesson list on success, or ``None`` on CLI
    failure / timeout. Callers should fall back to the existing
    exact-string dedup when this returns ``None``.

    The timeout is intentionally short (15s) — this runs on the write
    hot path after each agent loop iteration; we'd rather skip the
    smart dedup than block the loop.
    """
    if not existing_content.strip() or not new_lessons_text.strip():
        return new_lessons_text

    from app.cli_provider import build_full_command
    from app.config import get_model_config
    from app.prompts import load_prompt

    try:
        prompt = load_prompt(
            "learnings-dedup",
            EXISTING_CONTENT=existing_content,
            NEW_LESSONS=new_lessons_text,
        )
    except OSError as e:
        print(f"[pr_review_learning] dedup prompt load failed: {e}", file=sys.stderr)
        return None

    models = get_model_config()
    cmd = build_full_command(
        prompt=prompt,
        allowed_tools=[],
        model=models.get("lightweight", "haiku"),
        fallback=models.get("fallback", "sonnet"),
        max_turns=1,
    )

    from app.cli_exec import run_cli_with_retry

    try:
        # max_attempts=1: honor the 15s hot-path budget literally. The
        # exact-string fallback is good enough when this fails — we'd
        # rather skip the smart pass than burn 60s+ on retry backoff
        # (worst case with the default 3 attempts × 15s + 2+5+10s sleep).
        result = run_cli_with_retry(
            cmd,
            capture_output=True, text=True,
            timeout=timeout, cwd=project_path,
            max_attempts=1,
        )
        if result.returncode != 0:
            print(
                f"[pr_review_learning] dedup CLI failed (rc={result.returncode}): "
                f"{result.stderr[:200]}",
                file=sys.stderr,
            )
            return None
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError, RuntimeError) as e:
        print(f"[pr_review_learning] dedup CLI error: {e}", file=sys.stderr)
        return None


def _append_lessons_to_learnings(
    instance_dir: str,
    project_name: str,
    lessons_text: str,
    section_header: str = "PR review learnings",
    project_path: Optional[str] = None,
) -> int:
    """Append new lessons to the project's learnings.md, skipping duplicates.

    Two dedup passes happen in order:

    1. **Exact-string** dedup against existing lines (cheap, deterministic).
       Drops any candidate whose stripped line already appears verbatim.
    2. Optional **semantic** dedup via lightweight Claude CLI when
       ``memory.write_time_dedup`` is enabled (default), run only on
       candidates that survived pass 1. Catches paraphrases the
       exact-string pass would miss. Falls back transparently on CLI
       failure or timeout. A final exact-string sweep is applied to the
       CLI output to absorb any echoed existing lines.

    Running exact-string dedup first means the CLI call is skipped
    entirely when every candidate is an obvious duplicate (the common
    case in a quiet cycle) — keeps the agent loop hot path lean.

    Args:
        instance_dir: Path to the instance directory.
        project_name: Project name for scoping.
        lessons_text: Markdown bullet list from Claude analysis.
        section_header: Section title prefix (date is appended automatically).
        project_path: Project repo path used as cwd for the dedup CLI call.
            When ``None`` (or write-time dedup disabled) only exact-string
            dedup runs.

    Returns:
        Number of new lines appended.
    """
    from app.utils import atomic_write

    learnings_path = (
        Path(instance_dir) / "memory" / "projects" / project_name / "learnings.md"
    )

    # Read existing content
    existing_lines = set()
    existing_content = ""
    if learnings_path.exists():
        try:
            existing_content = learnings_path.read_text(encoding="utf-8")
            existing_lines = {
                line.strip()
                for line in existing_content.splitlines()
                if line.strip() and not line.strip().startswith("#")
            }
        except (OSError, UnicodeDecodeError) as e:
            print(f"[pr_review_learning] Error reading learnings: {e}", file=sys.stderr)

    # Pass 1: exact-string dedup (cheap, always runs).
    new_lines = [
        line for line in lessons_text.splitlines()
        if line.strip() and line.strip() not in existing_lines
    ]

    if not new_lines:
        return 0

    # Pass 2 (optional): semantic dedup via CLI, run only on the survivors.
    # Skipped when:
    #   - existing learnings file is empty (nothing to dedup against)
    #   - operator disabled it via memory.write_time_dedup = false
    #   - project_path is unknown (no cwd for the CLI call)
    if (
        project_path
        and existing_content.strip()
        and _is_write_time_dedup_enabled()
    ):
        filtered = _dedup_lessons_with_cli(
            "\n".join(new_lines), existing_content, project_path,
        )
        if filtered is not None:
            # Re-apply exact-string dedup to the CLI output in case the
            # model echoed an existing line back unchanged.
            new_lines = [
                line for line in filtered.splitlines()
                if line.strip() and line.strip() not in existing_lines
            ]

    if not new_lines:
        return 0

    # Ensure directory exists
    learnings_path.parent.mkdir(parents=True, exist_ok=True)

    # Build new content
    date_str = datetime.now().strftime("%Y-%m-%d")
    section = f"\n## {section_header} ({date_str})\n\n" + "\n".join(new_lines) + "\n"

    if existing_content:
        new_content = existing_content.rstrip("\n") + "\n" + section
    else:
        new_content = f"# Learnings — {project_name}\n" + section

    atomic_write(learnings_path, new_content)

    # Mirror each lesson to the JSONL truth log (one entry per lesson line)
    try:
        from app.memory_manager import append_memory_entry
        for line in new_lines:
            stripped = line.strip()
            if stripped:
                append_memory_entry(
                    instance_dir, "learning", project_name, stripped,
                    source_skill="review",
                )
    except Exception as e:
        log.warning("JSONL append failed: %s", e)

    return len(new_lines)


def learn_from_reviews(
    instance_dir: str,
    project_name: str,
    project_path: str,
    days: int = 30,
    limit: int = 20,
) -> dict:
    """Main entry point: fetch reviews, analyze with Claude, persist to learnings.md.

    This is the function called by the agent loop (e.g., from mission_runner
    or iteration_manager) after a session completes.

    Args:
        instance_dir: Path to the instance directory.
        project_name: Current project name.
        project_path: Path to the git repo.
        days: Look-back window.
        limit: Max PRs to analyze.

    Returns:
        Dict with keys: fetched (int), analyzed (bool), lessons_added (int),
        skipped_reason (str or None).
    """
    result = {"fetched": 0, "analyzed": False, "lessons_added": 0, "skipped_reason": None}

    # Process any review-findings sidecars for merged PRs
    try:
        _process_review_findings_sidecars(instance_dir, project_name, project_path)
    except (OSError, json.JSONDecodeError, subprocess.SubprocessError, RuntimeError) as exc:
        logging.exception("[pr_review_learning] sidecar processing failed: %s", exc)

    try:
        from app.config import get_review_calibration_config
        cal_cfg = get_review_calibration_config()
        _maybe_run_calibration_pass(
            instance_dir, project_name, project_path,
            batch_size=cal_cfg["batch_size"],
        )
    except (OSError, json.JSONDecodeError, RuntimeError, subprocess.SubprocessError) as exc:
        logging.exception("[pr_review_learning] calibration failed: %s", exc)

    try:
        from app.config import get_review_calibration_config as _get_cal_cfg
        _cleanup_stale_sidecars(
            instance_dir, stale_days=_get_cal_cfg()["stale_days"],
        )
    except (OSError, json.JSONDecodeError) as exc:
        logging.exception("[pr_review_learning] stale sidecar cleanup failed: %s", exc)

    prs = fetch_pr_reviews(project_path, days=days, limit=limit)
    result["fetched"] = len(prs)
    if not prs:
        result["skipped_reason"] = "no_reviews"
        return result

    # Check cache — skip if reviews haven't changed
    review_hash = _compute_review_hash(prs)
    if _is_cache_fresh(instance_dir, review_hash):
        result["skipped_reason"] = "cache_fresh"
        return result

    # Split into merged and rejected PRs
    merged_prs = [pr for pr in prs if pr.get("was_merged")]
    rejected_prs = [pr for pr in prs if not pr.get("was_merged")]

    total_added = 0
    any_analyzed = False
    any_empty = False

    # Analyze merged PRs with the standard prompt
    if merged_prs:
        merged_text = format_reviews_for_analysis(merged_prs)
        if merged_text:
            lessons = analyze_reviews_with_cli(merged_text, project_path)
            any_analyzed = True
            if lessons:
                total_added += _append_lessons_to_learnings(
                    instance_dir, project_name, lessons,
                    project_path=project_path)
            else:
                any_empty = True

    # Analyze rejected PRs with the dedicated rejection prompt
    if rejected_prs:
        rejected_text = format_reviews_for_analysis(rejected_prs)
        if rejected_text:
            lessons = _analyze_rejection_with_cli(rejected_text, project_path)
            any_analyzed = True
            if lessons:
                added = _append_lessons_to_learnings(
                    instance_dir, project_name, lessons,
                    section_header="Rejected PR learnings",
                    project_path=project_path)
                total_added += added
                _write_rejection_journal_entries(
                    instance_dir, project_name, rejected_prs, lessons)
            else:
                any_empty = True

    result["analyzed"] = any_analyzed

    if not any_analyzed:
        result["skipped_reason"] = "no_review_content"
        return result

    if total_added == 0 and any_empty:
        result["skipped_reason"] = "empty_analysis"
        count = _increment_failure_count(instance_dir)
        _notify_analysis_failures(instance_dir, count)
        return result

    # At least some analysis succeeded — reset failure counter
    if total_added > 0:
        _reset_failure_count(instance_dir)

    result["lessons_added"] = total_added
    _write_cache(instance_dir, review_hash)
    return result


def _analyze_rejection_with_cli(
    review_text: str,
    project_path: str,
) -> str:
    """Use Claude CLI with the rejection-specific prompt to extract lessons."""
    from app.cli_provider import build_full_command
    from app.config import get_model_config
    from app.prompts import load_prompt

    prompt = load_prompt("rejection-learning", REVIEW_DATA=review_text)
    models = get_model_config()

    cmd = build_full_command(
        prompt=prompt,
        allowed_tools=[],
        model=models.get("lightweight", "haiku"),
        fallback=models.get("fallback", "sonnet"),
        max_turns=1,
    )

    from app.cli_exec import run_cli_with_retry

    try:
        result = run_cli_with_retry(
            cmd,
            capture_output=True, text=True,
            timeout=60, cwd=project_path,
        )
        if result.returncode != 0:
            print(
                f"[pr_review_learning] Rejection analysis failed: {result.stderr[:200]}",
                file=sys.stderr,
            )
            return ""
        return result.stdout.strip()
    except Exception as e:
        print(f"[pr_review_learning] Rejection analysis error: {e}", file=sys.stderr)
        return ""


def _write_rejection_journal_entries(
    instance_dir: str,
    project_name: str,
    rejected_prs: List[dict],
    lessons_text: str,
) -> None:
    """Write journal entries for rejected PRs."""
    try:
        from app.journal import append_to_journal
    except ImportError:
        return

    first_lesson = ""
    for line in lessons_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            first_lesson = stripped[2:]
            break

    now = datetime.now().strftime("%H:%M")
    for pr in rejected_prs:
        title = pr.get("title", "untitled")
        number = pr.get("number", "?")
        reason = first_lesson or "No specific reason extracted"
        content = (
            f"## Rejected PR — {now}\n\n"
            f"PR #{number}: {title}\n"
            f"Reason: {reason}\n"
            f"Learning recorded in memory/projects/{project_name}/learnings.md\n"
        )
        try:
            append_to_journal(Path(instance_dir), project_name, content)
        except Exception as e:
            print(f"[pr_review_learning] Journal write failed for PR #{number}: {e}",
                  file=sys.stderr)


def _parse_iso(dt_str: str) -> Optional[datetime]:
    """Parse ISO datetime string."""
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Review findings outcome tracking
# ---------------------------------------------------------------------------

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_DIFF_FILE_RE = re.compile(r"^diff --git a/(.*) b/(.*)")
_FINDING_WINDOW = 3


def _parse_diff_touched_lines(diff: str) -> dict:
    """Parse a unified diff and return {file: set(line_numbers)} of modified old-file lines.

    Tracks old-file line positions so callers can compare against findings
    that reference old-file line numbers.  Only added (+) and removed (-)
    lines contribute; context lines are skipped.
    """
    touched: dict = {}
    current_file = None
    old_line_num = 0

    for line in diff.splitlines():
        m = _DIFF_FILE_RE.match(line)
        if m:
            current_file = m.group(2)
            if current_file not in touched:
                touched[current_file] = set()
            old_line_num = 0
            continue
        m = _HUNK_RE.match(line)
        if m and current_file is not None:
            old_line_num = int(m.group(1))
            continue
        if current_file is None or old_line_num == 0:
            continue
        if line.startswith("+"):
            touched[current_file].add(old_line_num)
        elif line.startswith("-"):
            touched[current_file].add(old_line_num)
            old_line_num += 1
        elif line.startswith(" "):
            old_line_num += 1
    return touched


def _compute_finding_outcomes(
    findings: list,
    diff: str,
) -> list:
    """Match review findings against a merge diff to determine if each was addressed.

    A finding is "addressed" if any hunk in the diff touches a line within
    ±_FINDING_WINDOW of the finding's line range in the same file.
    """
    if not findings:
        return []

    touched = _parse_diff_touched_lines(diff)

    results = []
    for f in findings:
        file_path = f.get("file", "")
        if not file_path:
            print(
                "[pr_review_learning] finding has no file path; "
                "skipping outcome to avoid skewing calibration",
                file=sys.stderr,
            )
            continue
        line_start = f.get("line_start", 0)
        line_end = f.get("line_end", line_start)
        low = line_start - _FINDING_WINDOW
        high = line_end + _FINDING_WINDOW

        if file_path not in touched:
            print(
                f"[pr_review_learning] finding file {file_path!r} absent from diff; "
                f"recording as not addressed",
                file=sys.stderr,
            )
        modified_lines = touched.get(file_path, set())
        addressed = any(low <= ln <= high for ln in modified_lines)

        results.append({
            "severity": f.get("severity", ""),
            "title": f.get("title", ""),
            "file": file_path,
            "line_start": line_start,
            "addressed": addressed,
        })
    return results


# Cap on review-outcomes.jsonl so it doesn't grow without bound across months
# of merged PRs. Generously larger than _MAX_CALIBRATION_LINES (the CLI feed
# cap) so calibration always has a full window to draw from, while keeping the
# per-cycle full read and on-disk size bounded.
_MAX_OUTCOMES_LINES = 1000


def _trim_outcomes_file(outcomes_path: Path) -> None:
    """Bound the outcomes log to the most recent _MAX_OUTCOMES_LINES entries.

    Drops the oldest lines when the file exceeds the cap and keeps the
    calibration marker consistent by subtracting the number of dropped lines
    from ``last_processed_lines`` (floored at 0), so an already-processed
    prefix that gets trimmed never makes the marker point past EOF.
    """
    if not outcomes_path.is_file():
        return
    try:
        lines = outcomes_path.read_text().splitlines()
    except OSError as exc:
        print(
            f"[pr_review_learning] failed to read outcomes for trim: {exc}",
            file=sys.stderr,
        )
        return
    if len(lines) <= _MAX_OUTCOMES_LINES:
        return

    dropped = len(lines) - _MAX_OUTCOMES_LINES
    kept = lines[-_MAX_OUTCOMES_LINES:]
    from app.utils import atomic_write, atomic_write_json
    try:
        atomic_write(outcomes_path, "\n".join(kept) + "\n")
    except OSError as exc:
        print(
            f"[pr_review_learning] failed to trim outcomes file: {exc}",
            file=sys.stderr,
        )
        return

    marker_path = outcomes_path.parent / ".review-calibration-marker.json"
    if not marker_path.is_file():
        return
    try:
        marker = json.loads(marker_path.read_text())
        last = int(marker.get("last_processed_lines", 0))
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return
    new_last = max(0, last - dropped)
    if new_last != last:
        try:
            atomic_write_json(marker_path, {"last_processed_lines": new_last})
        except OSError as exc:
            print(
                f"[pr_review_learning] failed to update calibration marker after "
                f"trim (marker may over/under-count unprocessed lines): {exc}",
                file=sys.stderr,
            )


def _process_review_findings_sidecars(
    instance_dir: str,
    project_name: str,
    project_path: str,
) -> int:
    """Process sidecar JSON files for merged PRs and write finding outcomes.

    Returns the number of sidecars successfully processed.
    """
    sidecar_dir = Path(instance_dir) / ".review-findings"
    if not sidecar_dir.is_dir():
        return 0

    sidecar_files = list(sidecar_dir.glob("*.json"))
    if not sidecar_files:
        return 0

    from app.github import run_gh

    processed = 0
    for sidecar_path in sidecar_files:
        try:
            data = json.loads(sidecar_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            print(
                f"[pr_review_learning] skipping unreadable sidecar {sidecar_path.name}: {exc}",
                file=sys.stderr,
            )
            continue

        sidecar_project = data.get("project_name", "")
        if sidecar_project != project_name:
            continue

        pr_key = data.get("pr_key", "")
        if not pr_key or "#" not in pr_key:
            print(
                f"[pr_review_learning] skipping sidecar {sidecar_path.name}: "
                f"invalid pr_key {pr_key!r}",
                file=sys.stderr,
            )
            continue

        repo_part, pr_number = pr_key.rsplit("#", 1)
        if "/" not in repo_part:
            print(
                f"[pr_review_learning] skipping sidecar {sidecar_path.name}: "
                f"malformed repo in pr_key {pr_key!r}",
                file=sys.stderr,
            )
            continue

        try:
            merged_json = run_gh(
                "pr", "view", pr_number,
                "--repo", repo_part,
                "--json", "mergedAt,headRefOid",
            )
        except (subprocess.SubprocessError, OSError, RuntimeError) as exc:
            print(f"[pr_review_learning] gh pr view failed for {pr_key}: {exc}", file=sys.stderr)
            continue

        try:
            pr_info = json.loads(merged_json)
            merged_at = pr_info.get("mergedAt", "")
        except (json.JSONDecodeError, TypeError):
            print(
                f"[pr_review_learning] malformed gh pr view response for {pr_key}",
                file=sys.stderr,
            )
            continue

        if not merged_at:
            continue

        final_head = pr_info.get("headRefOid", "")
        review_head = data.get("head_sha", "")
        file_comments = data.get("file_comments", [])

        if not file_comments:
            sidecar_path.unlink(missing_ok=True)
            processed += 1
            continue

        if not final_head or not review_head:
            missing = []
            if not final_head:
                missing.append("final_head")
            if not review_head:
                missing.append("review_head")
            print(
                f"[pr_review_learning] skipping sidecar {sidecar_path.name}: "
                f"missing SHA(s): {', '.join(missing)}",
                file=sys.stderr,
            )
            continue

        if final_head == review_head:
            outcomes_path = (
                Path(instance_dir) / "memory" / "projects"
                / project_name / "review-outcomes.jsonl"
            )
            outcomes_path.parent.mkdir(parents=True, exist_ok=True)
            ts = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())
            try:
                with open(outcomes_path, "a") as f:
                    for fc in file_comments:
                        # Mirror _compute_finding_outcomes: skip file-less
                        # findings so both paths handle them identically and
                        # calibration is not skewed by un-locatable findings.
                        if not fc.get("file"):
                            print(
                                "[pr_review_learning] finding has no file path; "
                                "skipping outcome to avoid skewing calibration",
                                file=sys.stderr,
                            )
                            continue
                        f.write(json.dumps({
                            "severity": fc.get("severity", ""),
                            "title": fc.get("title", ""),
                            "file": fc.get("file", ""),
                            "line_start": fc.get("line_start", 0),
                            "addressed": False,
                            "pr_key": pr_key,
                            "timestamp": ts,
                        }) + "\n")
            except OSError as exc:
                print(
                    f"[pr_review_learning] failed to write outcomes for {pr_key}: {exc}",
                    file=sys.stderr,
                )
                continue
            sidecar_path.unlink(missing_ok=True)
            processed += 1
            continue

        # Scope the diff to only the files referenced by findings.  A rebase
        # between review and merge replays unrelated upstream commits into the
        # review_head..final_head range; restricting to the finding files keeps
        # those unrelated changes from being recorded as "addressed".
        finding_files = sorted({
            fc.get("file", "") for fc in file_comments if fc.get("file")
        })
        # `final_head` (the merged headRefOid from GitHub) is only guaranteed to
        # be in the local object store if something fetched it. After branch
        # cleanup + git gc the SHA can be pruned, which would make `git diff`
        # exit non-zero and silently drop this PR — precisely the post-review
        # PRs most useful for calibration. Ensure the commit is present (with a
        # targeted fetch) before diffing, and surface an unresolvable SHA
        # distinctly from a transient diff failure.
        fetch_result = None
        try:
            present = subprocess.run(
                ["git", "cat-file", "-e", final_head],
                capture_output=True, text=True, cwd=project_path, timeout=30,
            )
            if present.returncode != 0:
                fetch_result = subprocess.run(
                    ["git", "fetch", "origin", final_head],
                    capture_output=True, text=True, cwd=project_path, timeout=30,
                )
                present = subprocess.run(
                    ["git", "cat-file", "-e", final_head],
                    capture_output=True, text=True, cwd=project_path, timeout=30,
                )
        except (subprocess.SubprocessError, OSError) as exc:
            print(
                f"[pr_review_learning] resolving final_head failed for {pr_key}: {exc}",
                file=sys.stderr,
            )
            continue
        if present.returncode != 0:
            if fetch_result is not None and fetch_result.returncode != 0:
                print(
                    f"[pr_review_learning] git fetch for {final_head[:12]} exited "
                    f"{fetch_result.returncode} for {pr_key} (remote/credentials issue, "
                    f"not a pruned SHA): {fetch_result.stderr.strip()[:200]}",
                    file=sys.stderr,
                )
            print(
                f"[pr_review_learning] final_head {final_head[:12]} unresolvable "
                f"for {pr_key} (pruned locally and not fetchable); dropping sidecar "
                f"so it is not retried every cycle",
                file=sys.stderr,
            )
            sidecar_path.unlink(missing_ok=True)
            processed += 1
            continue
        try:
            result = subprocess.run(
                ["git", "diff", f"{review_head}..{final_head}", "--", *finding_files],
                capture_output=True, text=True, cwd=project_path,
                timeout=30,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            print(f"[pr_review_learning] git diff failed for {pr_key}: {exc}", file=sys.stderr)
            continue
        if result.returncode != 0:
            print(
                f"[pr_review_learning] git diff exited {result.returncode} for {pr_key}: "
                f"{result.stderr[:200]}",
                file=sys.stderr,
            )
            continue

        outcomes = _compute_finding_outcomes(file_comments, result.stdout)

        outcomes_path = (
            Path(instance_dir) / "memory" / "projects"
            / project_name / "review-outcomes.jsonl"
        )
        outcomes_path.parent.mkdir(parents=True, exist_ok=True)
        ts = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())
        try:
            with open(outcomes_path, "a") as f:
                for outcome in outcomes:
                    outcome["pr_key"] = pr_key
                    outcome["timestamp"] = ts
                    f.write(json.dumps(outcome) + "\n")
        except OSError as exc:
            print(
                f"[pr_review_learning] failed to write outcomes for {pr_key}: {exc}",
                file=sys.stderr,
            )
            continue

        sidecar_path.unlink(missing_ok=True)
        processed += 1

    # Bound the outcomes log so it doesn't grow without limit across months of
    # merged PRs; the per-cycle full read in _maybe_run_calibration_pass and
    # the on-disk size both depend on this staying capped.
    outcomes_path = (
        Path(instance_dir) / "memory" / "projects"
        / project_name / "review-outcomes.jsonl"
    )
    _trim_outcomes_file(outcomes_path)

    return processed


def _cleanup_stale_sidecars(instance_dir: str, stale_days: int = 90) -> None:
    """Remove sidecar files older than stale_days to prevent unbounded growth."""
    sidecar_dir = Path(instance_dir) / ".review-findings"
    if not sidecar_dir.is_dir():
        return

    cutoff = _time.time() - (stale_days * 86400)
    for path in sidecar_dir.glob("*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except OSError as exc:
            print(
                f"[pr_review_learning] stale sidecar cleanup failed for {path.name}: {exc}",
                file=sys.stderr,
            )


def _maybe_run_calibration_pass(
    instance_dir: str,
    project_name: str,
    project_path: str,
    batch_size: int = 10,
) -> bool:
    """Run a calibration pass if enough unprocessed outcome entries exist.

    Returns True if calibration was run and hints were appended to learnings.md.
    """
    mem_dir = Path(instance_dir) / "memory" / "projects" / project_name
    outcomes_path = mem_dir / "review-outcomes.jsonl"
    if not outcomes_path.is_file():
        return False

    _MAX_CALIBRATION_LINES = 200

    lines = outcomes_path.read_text().strip().splitlines()
    total_lines = len(lines)
    if total_lines == 0:
        return False

    marker_path = mem_dir / ".review-calibration-marker.json"
    last_processed = 0
    if marker_path.is_file():
        try:
            marker = json.loads(marker_path.read_text())
            last_processed = int(marker.get("last_processed_lines", 0))
        except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
            print(
                f"[pr_review_learning] corrupt calibration marker for {project_name}, "
                f"recalibrating: {exc}",
                file=sys.stderr,
            )

    # Clamp a stale/corrupt marker that points past EOF so the window math
    # below stays in range.
    last_processed = max(0, min(last_processed, total_lines))

    unprocessed = total_lines - last_processed
    if unprocessed < batch_size:
        return False

    # Process unprocessed entries oldest-first, capped per pass. Feeding the
    # forward window (rather than the file tail) guarantees every outcome is
    # eventually analyzed even when more than _MAX_CALIBRATION_LINES accumulate
    # between passes, instead of silently skipping the older unprocessed ones.
    window = lines[last_processed:last_processed + _MAX_CALIBRATION_LINES]
    new_marker = last_processed + len(window)
    outcomes_text = "\n".join(window)
    result = _run_calibration_cli(outcomes_text, project_path)
    if result is None:
        # CLI failure (distinct from an empty-but-successful result): leave the
        # marker unadvanced so the batch is retried next cycle rather than
        # silently dropped.
        return False

    added = _append_lessons_to_learnings(
        instance_dir, project_name, result.strip(),
        section_header="Review calibration",
        project_path=project_path,
    )

    # Advance the marker by exactly the window we fed (not to EOF) so any
    # entries beyond the per-pass cap remain unprocessed and are analyzed on a
    # subsequent cycle. Advancing even when the pass produced no hints ("no
    # adjustments needed") avoids reprocessing the same batch every cycle.
    from app.utils import atomic_write_json
    atomic_write_json(marker_path, {"last_processed_lines": new_marker})

    return added > 0


def _run_calibration_cli(outcomes_jsonl: str, project_path: str) -> Optional[str]:
    """Run the review-calibration prompt via Claude CLI.

    Returns the CLI output text on success (possibly an empty/"no adjustments"
    string), or ``None`` on CLI failure so callers can distinguish a real
    failure from a genuinely empty result.
    """
    from app.prompts import load_prompt
    from app.provider import run_command

    prompt_template = load_prompt("review-calibration")
    date_str = _time.strftime("%Y-%m-%d")
    prompt = prompt_template.replace("{OUTCOMES_JSONL}", outcomes_jsonl)
    prompt = prompt.replace("{DATE}", date_str)

    try:
        return run_command(
            prompt=prompt,
            project_path=project_path,
            allowed_tools=[],
            model_key="lightweight",
            max_turns=1,
            timeout=60,
            max_turns_source=None,
        )
    except RuntimeError as exc:
        print(f"[pr_review_learning] calibration CLI failed: {exc}", file=sys.stderr)
        return None
