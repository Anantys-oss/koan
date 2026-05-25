"""
Kōan -- Implement runner.

Reads a GitHub issue containing a plan and invokes Claude to implement it.
The runner extracts the most recent plan iteration from the issue (body or
latest plan comment), ignoring older content, and feeds it to Claude with
an optional user-provided context (e.g. "Phase 1 to 3").

CLI:
    python3 -m skills.core.implement.implement_runner --project-path <path> --issue-url <url>
    python3 -m skills.core.implement.implement_runner --project-path <path> --issue-url <url> --context "Phase 1 to 3"
"""

import hashlib
import logging
import re
from pathlib import Path
from typing import List, Optional, Tuple, Union

from app.github import fetch_issue_with_comments
from app.github_url_parser import is_jira_url, parse_github_url, parse_issue_url, parse_jira_url
from app.pr_submit import (
    get_current_branch,
    guess_project_name,
    submit_draft_pr,
)
from app.prompts import load_prompt_or_skill

logger = logging.getLogger(__name__)

# Path to the plan skill directory (used for loading the plan-review prompt)
_PLAN_SKILL_DIR = Path(__file__).resolve().parent.parent / "plan"


# Regex pattern matching plan structure markers
_PLAN_MARKER_RE = re.compile(
    r"^#{2,}\s+(?:Implementation Phases|Phase \d+|Summary|Changes in this iteration)",
    re.MULTILINE | re.IGNORECASE,
)


def run_implement(
    project_path: str,
    issue_url: str,
    context: Optional[str] = None,
    notify_fn=None,
    skill_dir: Optional[Path] = None,
    base_branch: Optional[str] = None,
) -> Tuple[bool, str]:
    """Execute the implement pipeline.

    Fetches the GitHub issue, extracts the most recent plan, and invokes
    Claude to implement it.

    Args:
        project_path: Local path to the project repository.
        issue_url: GitHub issue URL containing the plan.
        context: Optional additional context (e.g. "Phase 1 to 3").
        notify_fn: Notification function (defaults to send_telegram).
        skill_dir: Path to the implement skill directory for prompt loading.

    Returns:
        (success, summary) tuple.
    """
    if notify_fn is None:
        from app.notify import send_telegram
        notify_fn = send_telegram

    context_label = f" ({context})" if context else ""
    _is_jira = is_jira_url(issue_url)

    # Parse URL and fetch issue content
    if _is_jira:
        try:
            issue_key = parse_jira_url(issue_url)
        except ValueError as e:
            return False, str(e)

        notify_fn(
            f"\U0001f528 Implementing Jira issue {issue_key}{context_label}..."
        )

        try:
            from app.jira_notifications import fetch_jira_issue
            title, body, comments = fetch_jira_issue(issue_key)
        except Exception as e:
            return False, f"Failed to fetch Jira issue: {str(e)[:300]}"

        owner, repo, issue_number = None, None, issue_key
    else:
        # Parse issue or PR URL (GitHub's issues API works for PRs too)
        try:
            owner, repo, _url_type, issue_number = parse_github_url(issue_url)
        except ValueError as e:
            return False, str(e)

        notify_fn(
            f"\U0001f528 Implementing issue #{issue_number} "
            f"({owner}/{repo}){context_label}..."
        )

        try:
            title, body, comments = fetch_issue_with_comments(
                owner, repo, issue_number
            )
        except Exception as e:
            return False, f"Failed to fetch issue: {str(e)[:300]}"

    # Extract the most recent plan
    plan = _extract_latest_plan(body, comments)
    label = issue_key if _is_jira else f"#{issue_number}"
    if not plan:
        return False, (
            f"No plan found in issue {label}. "
            "The issue should contain implementation phases."
        )

    # Plan-review quality gate with autonomous improvement loop
    gate_result = _run_plan_review_gate(
        plan, project_path, notify_fn=notify_fn, issue_url=issue_url,
    )
    if isinstance(gate_result, str):
        plan = gate_result
    elif gate_result is not None:
        return gate_result

    # Invoke Claude with the plan
    try:
        output = _execute_implementation(
            project_path=project_path,
            issue_url=issue_url,
            issue_title=title,
            plan=plan,
            context=context or "Implement the full plan.",
            skill_dir=skill_dir,
            issue_number=str(issue_number),
        )
    except Exception as e:
        return False, f"Implementation failed: {str(e)[:300]}"

    if not output:
        return False, "Claude returned empty output."

    # Post-implementation: submit draft PR (only for GitHub issues with repo info)
    pr_url = None
    if owner and repo:
        try:
            pr_url = _submit_implement_pr(
                project_path=project_path,
                owner=owner,
                repo=repo,
                issue_number=str(issue_number),
                issue_title=title,
                issue_url=issue_url,
                skill_dir=skill_dir,
                base_branch=base_branch,
            )
        except Exception as e:
            logger.warning("PR submission failed: %s", e)

    # Build notification and summary
    branch = get_current_branch(project_path)
    if pr_url:
        notify_fn(
            f"\u2705 Implementation complete for issue {label}"
            f"{context_label}\nDraft PR: {pr_url}"
        )
        summary = (
            f"Implementation complete for {label}{context_label}"
            f"\nDraft PR: {pr_url}"
        )
    elif branch not in ("main", "master"):
        notify_fn(
            f"\u2705 Implementation complete for issue {label}"
            f"{context_label}\nBranch: {branch}"
            f"{'' if pr_url else ' (PR creation skipped)' if _is_jira else ' (PR creation failed)'}"
        )
        summary = (
            f"Implementation complete for {label}{context_label}"
            f"\nBranch: {branch}"
        )
    else:
        notify_fn(
            f"\u26a0\ufe0f Implementation complete for issue {label}"
            f"{context_label} \u2014 changes landed on {branch}, no PR created"
        )
        summary = (
            f"Implementation complete for {label}{context_label}"
            f" (on {branch}, no PR)"
        )

    return True, summary


def _is_plan_content(text: str) -> bool:
    """Check if text contains plan structure markers.

    Args:
        text: Text to check for plan markers.

    Returns:
        True if text contains markdown headings indicating a plan structure.
    """
    if not text:
        return False
    return bool(_PLAN_MARKER_RE.search(text))


def _extract_latest_plan(body: Optional[str], comments: List[dict]) -> str:
    """Extract the most recent plan from issue body and comments.

    Strategy: scan comments from newest to oldest. The first comment
    that contains plan markers is the latest plan iteration. If no
    comment has a plan, fall back to the issue body.

    Args:
        body: Issue body text.
        comments: List of comment dicts with keys: author, date, body.

    Returns:
        The plan text, or empty string if no plan found.
    """
    # Check comments from newest to oldest
    for comment in reversed(comments):
        comment_body = comment.get("body", "")
        if _is_plan_content(comment_body):
            return comment_body

    # Fall back to issue body if it has plan markers
    if _is_plan_content(body):
        return body

    # If no plan markers found, assume the entire body is the plan
    # (allows non-standard plan formats). Body may be None for issues
    # with an empty body — GitHub returns body=null in that case.
    return (body or "").strip()


def _plan_hash(plan: str) -> str:
    """SHA-256 hex digest of the plan text (stripped)."""
    return hashlib.sha256(plan.strip().encode()).hexdigest()


def _plan_review_cache_path(project_path: str) -> Path:
    """Per-project cache file for the plan-review gate hash."""
    project_name = guess_project_name(project_path)
    from app.utils import KOAN_ROOT
    return KOAN_ROOT / "instance" / f".plan-review-hash-{project_name}"


def _is_plan_cache_fresh(project_path: str, current_hash: str) -> bool:
    """Return True if the cached plan hash matches — review can be skipped."""
    cache_path = _plan_review_cache_path(project_path)
    if not cache_path.exists():
        return False
    try:
        return cache_path.read_text().strip() == current_hash
    except OSError:
        return False


def _write_plan_cache(project_path: str, plan_hash_hex: str) -> None:
    """Persist the reviewed plan hash so identical re-runs skip review."""
    try:
        cache_path = _plan_review_cache_path(project_path)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        from app.utils import atomic_write
        atomic_write(cache_path, plan_hash_hex + "\n")
    except OSError as e:
        logger.warning("Plan-review cache write failed: %s", e)


def _run_plan_review_gate(
    plan: str,
    project_path: str,
    notify_fn=None,
    issue_url: str = "",
) -> Union[None, str, Tuple[bool, str]]:
    """Run plan-review gate with autonomous improvement loop.

    Returns:
        None — proceed with original plan (simple/cached/disabled).
        str — proceed with this improved plan (gate self-healed).
        (False, msg) — block (only on catastrophic internal error).
    """
    from app.plan_runner import improve_plan, is_simple_plan, review_plan

    if is_simple_plan(plan):
        logger.debug("Plan is simple — skipping review gate")
        return None

    from app.config import get_plan_review_config

    review_cfg = get_plan_review_config()
    if not review_cfg.get("implement_gate", True):
        return None

    current_hash = _plan_hash(plan)
    if _is_plan_cache_fresh(project_path, current_hash):
        logger.info("Plan-review gate: cache hit — skipping review")
        return None

    max_rounds = review_cfg.get("max_rounds", 3)
    current_plan = plan

    for round_num in range(1, max_rounds + 1):
        logger.info("Plan-review gate: round %d/%d...", round_num, max_rounds)
        approved, issues = review_plan(current_plan, project_path, _PLAN_SKILL_DIR)

        if approved:
            logger.info("Plan-review gate: APPROVED (round %d)", round_num)
            final_hash = _plan_hash(current_plan)
            _write_plan_cache(project_path, final_hash)
            if current_plan != plan:
                _post_improved_plan(current_plan, issue_url, notify_fn)
                return current_plan
            return None

        logger.info(
            "Plan-review gate: ISSUES_FOUND (round %d) — improving...",
            round_num,
        )

        if notify_fn and round_num == 1:
            try:
                notify_fn(
                    f"🔧 Plan review found issues — auto-improving "
                    f"(up to {max_rounds} rounds):\n{issues}"
                )
            except Exception:
                logger.debug("Failed to send improvement notification", exc_info=True)

        if round_num < max_rounds:
            current_plan = improve_plan(
                current_plan, issues, project_path, _PLAN_SKILL_DIR
            )

    # Exhausted all rounds — fail open, use best available plan
    logger.warning(
        "Plan-review gate: exhausted %d rounds — proceeding with best plan (fail open)",
        max_rounds,
    )
    if notify_fn:
        try:
            notify_fn(
                f"⚠️ Plan review couldn't fully resolve issues after {max_rounds} "
                "rounds — proceeding with implementation anyway (fail open)."
            )
        except Exception:
            logger.debug("Failed to send exhaustion notification", exc_info=True)

    if current_plan != plan:
        _post_improved_plan(current_plan, issue_url, notify_fn)
        return current_plan
    return None


def _post_improved_plan(
    improved_plan: str, issue_url: str, notify_fn=None,
) -> None:
    """Post the autonomously improved plan as a new comment on the issue."""
    if not issue_url:
        return
    try:
        from app.github import run_gh
        comment_body = (
            "### 🔧 Plan Improved (auto)\n\n"
            "The plan-review gate found issues and autonomously fixed them. "
            "Proceeding with this improved version:\n\n"
            f"{improved_plan}"
        )
        run_gh("issue", "comment", issue_url, "--body", comment_body)
    except Exception:
        logger.debug("Failed to post improved plan to GitHub", exc_info=True)


def _build_prompt(
    issue_url: str,
    issue_title: str,
    plan: str,
    context: str,
    skill_dir: Optional[Path] = None,
    branch_prefix: str = "koan/",
    issue_number: str = "",
    project_memory: str = "",
) -> str:
    """Build the implementation prompt from the issue and plan."""
    template_vars = dict(
        ISSUE_URL=issue_url,
        ISSUE_TITLE=issue_title,
        PLAN=plan,
        CONTEXT=context,
        BRANCH_PREFIX=branch_prefix,
        ISSUE_NUMBER=issue_number,
        PROJECT_MEMORY=project_memory,
    )

    return load_prompt_or_skill(skill_dir, "implement", **template_vars)


def _generate_pr_summary(
    project_path: str,
    issue_title: str,
    issue_url: str,
    commit_subjects: List[str],
    skill_dir: Optional[Path] = None,
) -> str:
    """Generate a PR summary using the lightweight model.

    Falls back to a bullet list of commit subjects if the model call
    fails or times out.
    """
    commits_text = "\n".join(f"- {s}" for s in commit_subjects) or "(no commits)"
    fallback = f"Implements {issue_url}\n\n{commits_text}"

    try:
        prompt = load_prompt_or_skill(
            skill_dir, "pr_summary",
            ISSUE_URL=issue_url,
            ISSUE_TITLE=issue_title,
            COMMIT_SUBJECTS=commits_text,
        )

        from app.cli_provider import run_command
        output = run_command(
            prompt, project_path,
            allowed_tools=[],
            model_key="lightweight",
            max_turns=1,
            timeout=300,
            max_turns_source=None,
        )
        return output.strip() if output and output.strip() else fallback
    except Exception as e:
        logger.debug("PR summary generation failed: %s", e)
        return fallback


def _execute_implementation(
    project_path: str,
    issue_url: str,
    issue_title: str,
    plan: str,
    context: str,
    skill_dir: Optional[Path] = None,
    issue_number: str = "",
) -> str:
    """Execute the implementation via Claude CLI."""
    from app.config import get_branch_prefix
    from app.skill_memory import build_memory_block_for_skill

    branch_prefix = get_branch_prefix()
    project_memory = build_memory_block_for_skill(
        project_path, f"{issue_title}\n{plan}",
    )

    prompt = _build_prompt(
        issue_url, issue_title, plan, context, skill_dir,
        branch_prefix=branch_prefix,
        issue_number=issue_number,
        project_memory=project_memory,
    )

    from app.cli_provider import CLAUDE_TOOLS, run_command_streaming
    from app.config import get_skill_max_turns, get_skill_timeout
    return run_command_streaming(
        prompt, project_path,
        allowed_tools=sorted(CLAUDE_TOOLS),
        max_turns=get_skill_max_turns(), timeout=get_skill_timeout(),
    )


# ---------------------------------------------------------------------------
# Post-implementation: draft PR submission (delegates to app.pr_submit)
# ---------------------------------------------------------------------------

def _submit_implement_pr(
    project_path: str,
    owner: str,
    repo: str,
    issue_number: str,
    issue_title: str,
    issue_url: str,
    skill_dir: Optional[Path] = None,
    base_branch: Optional[str] = None,
) -> Optional[str]:
    """Build implement-specific PR title/body and delegate to shared submit."""
    from app.pr_submit import get_commit_subjects
    from app.projects_config import resolve_base_branch

    project_name = guess_project_name(project_path)
    effective_base = base_branch or resolve_base_branch(project_name, project_path)
    commits = get_commit_subjects(project_path, base_branch=effective_base)

    summary = _generate_pr_summary(
        project_path, issue_title, issue_url, commits, skill_dir,
    )

    pr_title = f"Implement: {issue_title}"[:70]
    pr_body = (
        f"## Summary\n\n{summary}\n\n"
        f"Closes {issue_url}\n\n"
        f"---\n*Generated by Kōan /implement*"
    )

    try:
        from app.describe_pr import describe_pr, format_description
        desc = describe_pr(project_path, effective_base)
        if desc:
            pr_body = (
                f"{format_description(desc)}\n\n"
                f"Closes {issue_url}\n\n"
                f"---\n*Generated by Kōan /implement*"
            )
    except Exception as e:
        logger.warning("describe_pr failed, using fallback body: %s", e)

    try:
        return submit_draft_pr(
            project_path=project_path,
            project_name=project_name,
            owner=owner,
            repo=repo,
            issue_number=issue_number,
            pr_title=pr_title,
            pr_body=pr_body,
            issue_url=issue_url,
            base_branch=base_branch,
        )
    except Exception as e:
        logger.warning("PR submission failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# CLI entry point -- python3 -m app.implement_runner
# ---------------------------------------------------------------------------

def main(argv=None):
    """CLI entry point for implement_runner."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Implement a plan from a GitHub issue."
    )
    parser.add_argument(
        "--project-path", required=True,
        help="Local path to the project repository",
    )
    parser.add_argument(
        "--issue-url", required=True,
        help="GitHub issue URL containing the plan",
    )
    parser.add_argument(
        "--context",
        help="Additional context (e.g. 'Phase 1 to 3')",
        default=None,
    )
    parser.add_argument(
        "--base-branch",
        help="Target branch for the PR (e.g. '11.126')",
        default=None,
    )
    cli_args = parser.parse_args(argv)

    skill_dir = Path(__file__).resolve().parent

    success, summary = run_implement(
        project_path=cli_args.project_path,
        issue_url=cli_args.issue_url,
        context=cli_args.context,
        skill_dir=skill_dir,
        base_branch=cli_args.base_branch,
    )
    print(summary)
    return 0 if success else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
